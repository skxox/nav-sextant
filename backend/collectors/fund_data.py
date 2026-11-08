"""基金数据采集：单位净值、股票持仓。基于 AKShare，接口不稳定处做多路兜底。"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)


def fetch_fund_nav(fund_code: str, days: int = 400) -> pd.DataFrame:
    """返回列: trade_date(str YYYY-MM-DD), nav(float), accumulated_nav(float|None)"""
    try:
        import akshare as ak
        df = ak.fund_open_fund_info_em(symbol=fund_code, indicator="单位净值走势")
    except Exception as e:  # noqa: BLE001
        logger.warning("采集基金 %s 净值失败: %s", fund_code, e)
        return pd.DataFrame(columns=["trade_date", "nav", "accumulated_nav"])

    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "nav", "accumulated_nav"])

    # 兼容不同列名版本
    rename = {"净值日期": "trade_date", "单位净值": "nav", "累计净值": "accumulated_nav", "日增长率": "daily_growth"}
    df = df.rename(columns=rename)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
    df["nav"] = pd.to_numeric(df["nav"], errors="coerce")
    if "accumulated_nav" not in df.columns:
        df["accumulated_nav"] = None
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    df = df[df["trade_date"] >= cutoff]
    return df[["trade_date", "nav", "accumulated_nav"]].sort_values("trade_date")


def fetch_fund_holdings(fund_code: str) -> pd.DataFrame:
    """最新一期前十大重仓股。返回列: report_date, stock_code, stock_name, weight(%)"""
    years = [date.today().year, date.today().year - 1]
    frames = []
    try:
        import akshare as ak
        for y in years:
            try:
                df = ak.fund_portfolio_hold_em(symbol=fund_code, date=str(y))
                if df is not None and not df.empty:
                    frames.append(df)
            except Exception:  # noqa: BLE001,S110
                continue
    except ImportError as e:
        logger.error("akshare 未安装: %s", e)
        return pd.DataFrame(columns=["report_date", "stock_code", "stock_name", "weight"])

    if not frames:
        logger.warning("采集基金 %s 持仓失败", fund_code)
        return pd.DataFrame(columns=["report_date", "stock_code", "stock_name", "weight"])

    df = pd.concat(frames)
    rename = {"季度": "quarter", "股票代码": "stock_code", "股票名称": "stock_name", "占净值比例": "weight"}
    df = df.rename(columns=rename)
    df["weight"] = pd.to_numeric(df["weight"], errors="coerce")
    df = df.dropna(subset=["weight"])
    # 取最新季度
    latest_quarter = df["quarter"].max()
    df = df[df["quarter"] == latest_quarter]
    # 季度名如 "2024年4季度股票投资明细" → 报告期取 12-31/09-30 等
    q_map = {"1": "03-31", "2": "06-30", "3": "09-30", "4": "12-31"}
    year_part = "".join(ch for ch in str(latest_quarter) if ch.isdigit())[:4]
    q_digit = "".join(ch for ch in str(latest_quarter) if ch.isdigit())[4:5]
    report_date = f"{year_part}-{q_map.get(q_digit, '12-31')}"
    df = df.assign(report_date=report_date)
    return df[["report_date", "stock_code", "stock_name", "weight"]].head(10)
