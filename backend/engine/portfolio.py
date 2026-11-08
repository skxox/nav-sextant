"""组合层辅助计算。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..db import Database


def portfolio_drawdown(db: Database, nav_history: pd.DataFrame) -> float | None:
    """以单基金净值历史近似组合回撤（个人持有场景多为单基金轮动）。"""
    if nav_history is None or len(nav_history) < 2:
        return None
    nav = nav_history.sort_values("trade_date")["nav"].astype(float).to_numpy()
    peak = np.maximum.accumulate(nav)
    dd = float(((nav[-1] - peak[-1]) / peak[-1]))
    return dd if dd < 0 else 0.0
