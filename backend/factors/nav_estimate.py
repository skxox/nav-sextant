"""T-1 净值估算：基于最新一期持仓权重 + 指数近两日涨跌做加权估算。

对国内场外基金，公开数据只有 T-1 官方净值；这里用重仓股对应的指数涨跌
近似估算"今天的净值"，作为消息面/风险面的参考输入。精度有限，仅作辅助。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class NavEstimate:
    fund_code: str
    latest_official_date: str | None
    latest_official_nav: float | None
    estimated_change_pct: float | None  # 相对最新官方净值的估算涨跌
    estimated_nav: float | None


def estimate_nav(nav_history: pd.DataFrame, holdings: pd.DataFrame,
                 index_close: pd.DataFrame) -> NavEstimate | None:
    """nav_history: trade_date, nav；holdings: weight；index_close: trade_date, close"""
    if nav_history is None or nav_history.empty:
        return None
    latest = nav_history.sort_values("trade_date").iloc[-1]
    est = NavEstimate(
        fund_code="", latest_official_date=str(latest["trade_date"]),
        latest_official_nav=float(latest["nav"]), estimated_change_pct=None, estimated_nav=None,
    )
    if index_close is None or index_close.empty or holdings is None or holdings.empty:
        return est

    idx = index_close.sort_values("trade_date").tail(2)
    if len(idx) < 2:
        return est
    index_change = (float(idx.iloc[-1]["close"]) / float(idx.iloc[-2]["close"]) - 1) * 100
    # 持仓集中度折算：前十大通常占 40%~60%，按持仓比例加权的市场暴露
    total_weight = float(holdings["weight"].sum())
    if total_weight <= 0:
        return est
    est.estimated_change_pct = round(index_change * total_weight / 100, 3)
    est.estimated_nav = round(est.latest_official_nav * (1 + est.estimated_change_pct / 100), 4)
    return est
