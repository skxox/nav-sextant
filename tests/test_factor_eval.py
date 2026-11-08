"""因子检验引擎单元测试：合成数据下 RankIC 应符合理论值。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.engine.factor_eval import evaluate_factor, evaluate_all_factors


def test_perfect_factor_rank_ic():
    """因子与未来收益完全正相关 → RankIC ≈ 1，t 显著。

    注意：须用"增速递增"的几何净值 —— 线性递增净值的百分比收益是递减的，
    与递增因子恰好构成完美负相关（IC=-1），检验本身会正确地报告"显著反向"。
    """
    dates = [f"d{i:04d}" for i in range(130)]
    factor = {d: float(i) for i, d in enumerate(dates)}
    rets = 0.001 * (1 + np.arange(len(dates) + 10) / len(dates))
    nav = pd.Series(np.cumprod(1 + rets), index=[f"d{i:04d}" for i in range(len(dates) + 10)])
    rep = evaluate_factor(factor, nav, "测试因子", "test", forward_days=5, step=5)
    assert rep.samples >= 15
    assert rep.rank_ic > 0.95
    assert abs(rep.ic_tstat) > 3
    assert rep.verdict in ("显著有效", "方向正确")


def test_noise_factor_low_ic():
    """纯噪声因子 → IC 接近 0，结论不应是显著有效。"""
    rng = np.random.default_rng(3)
    dates = [f"d{i:04d}" for i in range(200)]
    factor = {d: float(rng.normal()) for d in dates}
    nav = pd.Series(np.cumprod(1 + rng.normal(0.0005, 0.01, len(dates) + 10)),
                    index=[f"d{i:04d}" for i in range(len(dates) + 10)])
    rep = evaluate_factor(factor, nav, "噪声", "noise", forward_days=5, step=5)
    assert abs(rep.rank_ic) < 0.4
    assert rep.verdict != "显著有效" or abs(rep.ic_tstat) < 3


def test_insufficient_samples():
    rep = evaluate_factor({"d0": 0.5, "d1": 0.6}, pd.Series([1.0, 1.1, 1.2], index=["d0", "d1", "d2"]),
                          "不足", "x", forward_days=5, step=5)
    assert rep.verdict == "样本不足" and rep.samples <= 2


def test_evaluate_all_factors_end_to_end():
    """合成上涨净值 + 正弦 PE：三个因子报告都能产出且字段完整。"""
    from tests.conftest import make_nav_series, make_pe_series
    nav = make_nav_series(300)
    pe = make_pe_series(600)
    pe["close"] = np.linspace(3000, 3600, len(pe))
    reports = evaluate_all_factors(nav, pe, forward_days=5)
    assert len(reports) >= 2
    for r in reports:
        assert r.factor in ("momentum", "risk_lowvol", "valuation")
        assert -1.0 <= r.rank_ic <= 1.0
        assert r.verdict
