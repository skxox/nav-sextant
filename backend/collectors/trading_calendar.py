"""A股交易日历：跳过周末与节假日，流水线只在交易日产出信号。"""
from __future__ import annotations

import functools
import logging
from datetime import date, timedelta

import pandas as pd

from ..utils.retry import retry_on_error

logger = logging.getLogger(__name__)


@retry_on_error(attempts=3, base_delay=2.0)
def _fetch_trade_dates() -> pd.DatetimeIndex:
    import akshare as ak
    df = ak.tool_trade_date_hist_sina()
    s = pd.to_datetime(df["trade_date"])
    return pd.DatetimeIndex(sorted(s.unique()))


@functools.cache
def trade_dates() -> set[str]:
    """全部交易日（YYYY-MM-DD）。获取失败时退化为"周一到周五"规则。"""
    try:
        return {d.strftime("%Y-%m-%d") for d in _fetch_trade_dates()}
    except Exception as e:  # noqa: BLE001
        logger.warning("交易日历获取失败，退化为周末规则: %s", e)
        days = set()
        d = date.today() - timedelta(days=400)
        while d <= date.today() + timedelta(days=1):
            if d.weekday() < 5:
                days.add(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        return days


def is_trading_day(d: date) -> bool:
    return d.strftime("%Y-%m-%d") in trade_dates()


def last_trading_day(ref: date | None = None) -> date:
    """ref 当日或之前最近的交易日。"""
    d = ref or date.today()
    for _ in range(30):
        if is_trading_day(d):
            return d
        d -= timedelta(days=1)
    raise RuntimeError("30 天内找不到交易日，交易日历数据异常")
