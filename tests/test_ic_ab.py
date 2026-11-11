"""IC 调权 A/B 对照测试：走查重放 + 判定规则 + 无未来数据（ROADMAP A 组）。"""
from __future__ import annotations

import pandas as pd
import pytest

from backend.config import FundSpec, Settings
from backend.db import Database
from backend.engine.ic_ab import (
    ArmStat, _arm_stats, _churn, _verdict, ic_ab_test,
)
from tests.conftest import make_nav_series, make_pe_series

FUND = FundSpec(code="110022", name="测试基金", benchmark_index="000300")


def _seed_fund(db: Database, n_nav: int = 300, drift: float = 0.0008) -> pd.DataFrame:
    """净值 + 指数估值入库（同起点的连续工作日），返回净值 df。"""
    nav = make_nav_series(n=n_nav, drift=drift, seed=11)
    db.upsert_nav([(FUND.code, r.trade_date, float(r.nav), float(r.nav))
                   for r in nav.itertuples()])
    pe = make_pe_series(n=n_nav, seed=3)
    db.executemany_safe(
        "INSERT OR REPLACE INTO index_daily (index_code, trade_date, pe, pb) VALUES (?,?,?,?)",
        [("000300", r.trade_date, float(r.pe), float(r.pb)) for r in pe.itertuples()])
    return nav


# ---------- 判定规则 ----------

def _arm(label: str, n: int, avg: float) -> ArmStat:
    return ArmStat(label=label, actionable=n, evaluated=n, win_rate=0.5,
                   avg_return_pct=avg, t_stat=1.0)


def test_verdict_insufficient():
    v, delta = _verdict(_arm("关闭", 7, 0.5), _arm("开启", 9, 1.0))
    assert "样本不足" in v and delta is None
    v2, _ = _verdict(None, _arm("开启", 20, 1.0))
    assert "样本不足" in v2


def test_verdict_no_significant_diff():
    v, delta = _verdict(_arm("关闭", 20, 0.50), _arm("开启", 20, 0.62))
    assert "无显著差异" in v and delta == pytest.approx(0.12)


def test_verdict_direction():
    v_better, d1 = _verdict(_arm("关闭", 20, 0.2), _arm("开启", 20, 1.1))
    assert "IC 调权更优" in v_better and d1 > 0
    v_worse, d2 = _verdict(_arm("关闭", 20, 1.1), _arm("开启", 20, 0.2))
    assert "关闭更优" in v_worse and d2 < 0


def test_churn_counts_action_changes():
    sigs = [("d1", "BUY"), ("d2", "BUY"), ("d3", "SELL"), ("d4", "HOLD"), ("d5", "HOLD")]
    assert _churn(sigs) == 2


def test_arm_stats_directional_returns():
    """BUY 后上涨 → 正收益；SELL 同行情 → 取负（复盘口径）。"""
    navs = [("2026-01-01", 1.0), ("2026-01-02", 1.0), ("2026-01-03", 1.1), ("2026-01-04", 1.1)]
    buy = _arm_stats("开启", [("2026-01-01", "BUY")], navs, horizon=1)
    sell = _arm_stats("开启", [("2026-01-01", "SELL")], navs, horizon=1)
    hold = _arm_stats("开启", [("2026-01-01", "HOLD")], navs, horizon=1)
    assert buy.avg_return_pct == pytest.approx(10.0)
    assert sell.avg_return_pct == pytest.approx(-10.0)
    assert hold.actionable == 0 and hold.evaluated == 0


# ---------- 走查重放 ----------

def test_insufficient_history_reports_and_skips(tmp_path):
    db = Database(tmp_path / "ic.db")
    db.upsert_nav([(FUND.code, f"2026-01-{i:02d}", 1.0, None) for i in range(1, 10)])  # 9 日
    r = ic_ab_test(db, [FUND], Settings(), horizon=3)
    assert r.funds[0].signal_days == 0 and "净值历史不足" in r.funds[0].verdict


def test_replay_structure_and_determinism(tmp_path):
    db = Database(tmp_path / "ic.db")
    _seed_fund(db, n_nav=300)
    r1 = ic_ab_test(db, [FUND], Settings(), horizon=5).to_dict()
    r2 = ic_ab_test(db, [FUND], Settings(), horizon=5).to_dict()
    assert r1 == r2, "同库重放必须逐字节一致（确定性因子 + 固定规则）"

    f = r1["funds"][0]
    assert 30 <= f["signal_days"] <= 45          # 300 日净值 → 约 40 个信号日
    assert f["off"] and f["on"]                  # 两臂统计都在
    assert 0 <= f["agreement_rate"] <= 1
    assert f["ic_active_days"] >= 0
    assert f["verdict"]


def test_replay_allows_manual_horizon(tmp_path):
    db = Database(tmp_path / "ic.db")
    _seed_fund(db, n_nav=200)
    r = ic_ab_test(db, [FUND], Settings(), horizon=3)
    assert r.horizon == 3 and r.horizon_source == "manual"


def test_pooled_needs_double_samples(tmp_path):
    """合并结论门槛 16 条：300 日重放的样本不足时不下开关结论。"""
    db = Database(tmp_path / "ic.db")
    _seed_fund(db, n_nav=200)
    r = ic_ab_test(db, [FUND], Settings(), horizon=5)
    assert r.pooled_off is not None and r.pooled_on is not None
    if min(r.pooled_off.evaluated, r.pooled_on.evaluated) < 16:
        assert "样本不足" in r.verdict
