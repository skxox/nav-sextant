"""盘中实时估算（WealthAgent 多策略估值思路）。

按基金类型分流：
- 跟踪基准指数的基金（指数/LOF）→ 直接用指数实时涨跌
- 主动管理基金 → 前十大重仓权重 × 基准指数实时涨跌（持仓穿透近似）

实时行情源：东方财富指数行情（akshare），盘中数据延迟约数秒到一分钟。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from ..utils.retry import retry_on_error

logger = logging.getLogger(__name__)


@dataclass
class IntradayEstimate:
    fund_code: str
    fund_name: str
    index_code: str
    index_change_pct: float | None
    estimated_change_pct: float | None
    estimated_nav: float | None
    latest_nav: float | None
    latest_nav_date: str | None
    as_of: str


@retry_on_error(attempts=2, base_delay=1.0)
def _index_spot_change_pct(index_code: str) -> float | None:
    """指数实时涨跌幅。三级兜底：东财 → 新浪 → 腾讯。

    东财「沪深重要指数」只有宽基，行业指数（如 399997 中证白酒、000930 中证消费）
    需新浪；新浪仍缺的（000930 等）由腾讯 qt.gtimg.cn 兜底，CSI 系全覆盖。
    """
    import akshare as ak

    for fetch in (
        lambda: ak.stock_zh_index_spot_em(symbol="沪深重要指数"),
        lambda: ak.stock_zh_index_spot_sina(),
    ):
        try:
            df = fetch()
        except Exception:  # noqa: BLE001
            continue
        row = df[df["代码"].astype(str).str.lstrip("sh").str.lstrip("sz") == index_code]
        if not row.empty:
            return float(row.iloc[0]["涨跌幅"])

    return _tencent_index_change(index_code)


def _tencent_index_change(index_code: str) -> float | None:
    """腾讯指数行情：一次请求多只，字段~分隔，涨跌幅在第 33 列。CSI 系指数全覆盖。"""
    import requests

    from .market_data import _sina_symbol

    symbol = _sina_symbol(index_code)
    try:
        resp = requests.get(f"http://qt.gtimg.cn/q={symbol}", timeout=8)
        fields = resp.content.decode("gbk").split('="')
        if len(fields) < 2:
            return None
        parts = fields[1].split("~")
        if len(parts) > 32 and parts[32]:
            return float(parts[32])
    except Exception as e:  # noqa: BLE001
        logger.warning("腾讯指数行情失败（%s）: %s", index_code, e)
    return None


def estimate_intraday(db, fund_code: str, fund_name: str, benchmark_index: str,
                      latest_nav: float | None, latest_nav_date: str | None) -> IntradayEstimate:
    index_chg = None
    try:
        index_chg = _index_spot_change_pct(benchmark_index)
    except Exception as e:  # noqa: BLE001
        logger.warning("指数 %s 实时行情获取失败: %s", benchmark_index, e)

    holdings = db.query(
        "SELECT weight FROM fund_holdings WHERE fund_code=? ORDER BY report_date DESC LIMIT 10",
        (fund_code,))
    total_weight = sum(float(r["weight"]) for r in holdings) if holdings else 0.0

    est_chg = None
    if index_chg is not None and total_weight > 0:
        est_chg = round(index_chg * total_weight / 100, 3)
    est_nav = round(latest_nav * (1 + est_chg / 100), 4) if (latest_nav and est_chg is not None) else None

    return IntradayEstimate(
        fund_code=fund_code, fund_name=fund_name, index_code=benchmark_index,
        index_change_pct=index_chg, estimated_change_pct=est_chg,
        estimated_nav=est_nav, latest_nav=latest_nav, latest_nav_date=latest_nav_date,
        as_of=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
