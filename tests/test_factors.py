"""因子引擎单元测试。"""
from __future__ import annotations

import numpy as np

from backend.factors.momentum import compute_momentum
from backend.factors.valuation import compute_valuation


def test_momentum_uptrend_scores_positive(nav_df):
    mf = compute_momentum(nav_df)
    assert -1 <= mf.score <= 1
    assert mf.score > 0, "漂移为正的随机游走，多周期动量分应为正"
    assert set(mf.window_returns) == {5, 20, 60}


def test_momentum_insufficient_data():
    import pandas as pd
    mf = compute_momentum(pd.DataFrame({"trade_date": ["2025-01-01"], "nav": [1.0]}))
    assert mf.score == 0.0 and not mf.window_returns


def test_valuation_low_percentile_scores_positive(pe_df):
    vf = compute_valuation(pe_df, window=750)
    assert vf.current_pe is not None
    assert 0 <= vf.pe_percentile <= 100
    # 用末值构造一个处于低分位的序列（PE 与 PB 同时压到最低）
    low = pe_df.copy()
    low.iloc[-1, low.columns.get_loc("pe")] = float(pe_df["pe"].min()) - 1
    low.iloc[-1, low.columns.get_loc("pb")] = float(pe_df["pb"].min()) - 0.1
    vf_low = compute_valuation(low, window=750)
    assert vf_low.pe_percentile == 0.0
    assert vf_low.score > 0.9


def test_valuation_empty():
    import pandas as pd
    vf = compute_valuation(pd.DataFrame(columns=["trade_date", "pe", "pb"]))
    assert vf.score == 0.0 and vf.pe_percentile is None
