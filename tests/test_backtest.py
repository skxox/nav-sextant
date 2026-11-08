"""回测引擎测试：无前视偏差、指标合理。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.db import Database
from backend.engine.backtest import run_backtest


def _seed_db(tmp_path, monkeypatch):
    """内存化 DB 并注入合成数据。"""
    db = Database(tmp_path / "bt.db")
    rng = np.random.default_rng(3)
    n = 300
    rets = rng.normal(0.0008, 0.012, n)
    nav = 1.0 * np.cumprod(1 + rets)
    dates = pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d")
    db.upsert_nav([("110022", d, float(v), None) for d, v in zip(dates, nav)])
    pe = 12 + 3 * np.sin(np.linspace(0, 8, n))
    db.upsert_index([("000300", d, None, float(p), None, None, None) for d, p in zip(dates, pe)])
    return db, dates


def test_backtest_runs_and_metrics_sane(tmp_path):
    from backend.config import AppConfig, FundSpec, LLMSettings
    db, dates = _seed_db(tmp_path, None)
    cfg = AppConfig(
        funds=[FundSpec(code="110022", name="测试基金", benchmark_index="000300")],
        llm=LLMSettings(model="m", api_key="k"),
        db_path=tmp_path / "bt.db",
    )
    result = run_backtest(db, cfg.funds[0], cfg)
    d = result.to_dict()
    assert d["trading_days"] > 200
    assert d["start_date"] == dates[0]
    assert -100 < d["max_drawdown_pct"] <= 0
    assert len(d["strategy_curve"]) == d["trading_days"]
    assert d["trade_count"] >= 0


def test_backtest_no_lookahead(tmp_path):
    """策略净值曲线的第一天不应产生超额收益（T 日信号 T+1 生效）。"""
    from backend.config import AppConfig, FundSpec, LLMSettings
    db, _ = _seed_db(tmp_path, None)
    cfg = AppConfig(
        funds=[FundSpec(code="110022", name="t", benchmark_index="000300")],
        llm=LLMSettings(model="m", api_key="k"), db_path=tmp_path / "bt.db",
    )
    result = run_backtest(db, cfg.funds[0], cfg)
    first = result.curve[0]
    # 初始净值应以 1.0 起步（成本与收益都尚未发生或极小）
    assert abs(first["nav"] - 1.0) < 0.02
