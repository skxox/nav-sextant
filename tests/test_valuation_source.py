"""估值数据层测试：入库不互踩、蛋卷解析、分位三级降级。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.collectors.market_data import _fetch_danjuan_valuation
from backend.db import Database
from backend.factors.valuation import compute_valuation


# ---------- DB：估值入库不得踩掉 OHLCV ----------

def test_upsert_index_preserves_ohlcv(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_index_ohlcv([("399997", "2026-11-05", 6800.0, 6900.0, 6700.0, 6850.0, 1.2e6)])
    # 估值随后入库（close 恒为 NULL）
    db.upsert_index([("399997", "2026-11-05", None, 22.95, 4.30, 26.8, 5.0)])
    row = db.query("SELECT * FROM index_daily WHERE index_code='399997'")[0]
    assert row["pe"] == 22.95 and row["pe_percentile"] == 26.8
    assert row["close"] == 6850.0 and row["open"] == 6800.0, "估值入库不得抹掉 K 线列"


def test_upsert_index_null_pe_keeps_old(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_index([("000300", "2026-11-05", None, 13.2, 1.33, None, None)])
    # 第二次入库该行 pe 为 NULL（outer join 缺口）→ 保留旧值
    db.upsert_index([("000300", "2026-11-05", None, None, 1.34, None, None)])
    row = db.query("SELECT * FROM index_daily WHERE index_code='000300'")[0]
    assert row["pe"] == 13.2 and row["pb"] == 1.34


def test_upsert_index_ohlcv_preserves_valuation(tmp_path):
    db = Database(tmp_path / "t.db")
    db.upsert_index([("000300", "2026-11-05", None, 13.2, 1.33, 55.0, 40.0)])
    db.upsert_index_ohlcv([("000300", "2026-11-05", 3900.0, 3950.0, 3880.0, 3920.0, 9e7)])
    row = db.query("SELECT * FROM index_daily WHERE index_code='000300'")[0]
    assert row["close"] == 3920.0 and row["pe"] == 13.2 and row["pe_percentile"] == 55.0


# ---------- 蛋卷快照解析（离线：monkeypatch requests） ----------

_DANJUAN_PAYLOAD = {
    "data": {"items": [
        {"index_code": "SH000300", "name": "沪深300", "pe": 13.28, "pb": 1.33,
         "pe_percentile": 0.7316, "pb_percentile": 0.5102, "ts": 1793808000000},
        {"index_code": "SZ399997", "name": "中证白酒", "pe": 22.945, "pb": 4.3012,
         "pe_percentile": 0.2684, "pb_percentile": 0.0496, "ts": 1793808000000},
    ]}}


def test_danjuan_snapshot_parsing(tmp_path, monkeypatch):
    class _Resp:
        def raise_for_status(self): ...
        def json(self): return _DANJUAN_PAYLOAD
    import backend.collectors.market_data as md
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
    df = _fetch_danjuan_valuation("399997")
    assert len(df) == 1
    row = df.iloc[0]
    assert row["pe"] == 22.945 and row["pb"] == 4.3012
    assert row["pe_percentile"] == 26.8 and row["pb_percentile"] == 5.0
    assert str(row["trade_date"]).startswith("2026-11"), "ts 毫秒时间戳按上海时区转交易日"


def test_danjuan_unknown_index_returns_empty(tmp_path, monkeypatch):
    class _Resp:
        def raise_for_status(self): ...
        def json(self): return _DANJUAN_PAYLOAD
    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp())
    df = _fetch_danjuan_valuation("999999")
    assert df.empty


# ---------- compute_valuation 分位三级降级 ----------

def _hist_df(n: int, with_pe: bool = True) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d")
    return pd.DataFrame({
        "trade_date": dates,
        "pe": (np.linspace(10, 20, n) if with_pe else [None] * n),
        "pb": np.linspace(1, 2, n),
        "close": np.linspace(3000, 4000, n),
    })


def test_valuation_external_percentile_when_history_short():
    df = _hist_df(30)  # 自有 PE 历史 30 行 < 60
    df["pe_percentile"] = [None] * 29 + [26.8]
    df["pb_percentile"] = [None] * 29 + [5.0]
    vf = compute_valuation(df)
    assert vf.percentile_source == "external"
    assert vf.pe_percentile == 26.8 and vf.pb_percentile == 5.0
    assert not vf.price_proxy
    assert vf.erp_percentile is None, "外部分位模式无 ERP 历史分位"
    assert vf.data_quality > 0.45, "外部分位数据质量应高于价格代理"
    assert vf.score > 0, "低分位（低估）应为正分"


def test_valuation_history_wins_over_external():
    df = _hist_df(120)
    df["pe_percentile"] = [None] * 119 + [5.0]  # 外部分位故意偏离
    vf = compute_valuation(df)
    assert vf.percentile_source == "history"
    assert vf.pe_percentile != 5.0, "自有历史充分时用自己的分位"


def test_valuation_proxy_still_fallback():
    df = _hist_df(80, with_pe=False)  # 完全没有 PE → 价格代理
    vf = compute_valuation(df)
    assert vf.price_proxy and vf.percentile_source == "proxy"
    assert vf.data_quality == 0.45


def test_valuation_none_when_nothing_available():
    df = _hist_df(30, with_pe=False)
    vf = compute_valuation(df)
    assert vf.percentile_source == "none" and vf.pe_percentile is None
