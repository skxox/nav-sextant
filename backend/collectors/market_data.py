"""指数与宏观数据采集：指数收盘价、PE/PB 历史序列、新闻资讯。"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

# 常见指数名称映射（乐咕等数据源用中文名）
INDEX_NAME_MAP = {
    "000300": "沪深300",
    "000905": "中证500",
    "000001": "上证指数",
    "399006": "创业板指",
    "000015": "上证50",
    "399997": "中证白酒",
    "000930": "中证消费",
    "399986": "中证银行",
    "000827": "中证环保",
    "399998": "中证煤炭",
    "000825": "中证电子",
    "399975": "证券公司",
    "000813": "细分化工",
    "000815": "细分有色",
    "399967": "中证军工",
    "000991": "全指医药",
    "000992": "全指金融",
    "399989": "中证医疗",
    "931071": "中证新能源",
    "000979": "中证全指证券",
}


def index_name(index_code: str) -> str:
    return INDEX_NAME_MAP.get(index_code, f"指数{index_code}")


def fetch_index_close(index_code: str, days: int = 900) -> pd.DataFrame:
    """指数日线收盘价。返回列: trade_date, close"""
    df = fetch_index_ohlcv(index_code, days)
    if df.empty:
        return df[["trade_date", "close"]] if "close" in df.columns else pd.DataFrame(columns=["trade_date", "close"])
    return df[["trade_date", "close"]]


def _sina_symbol(index_code: str) -> str:
    """新浪指数代码前缀：399 开头为深交所（sz），其余默认上交所（sh）。"""
    if index_code.startswith(("sh", "sz")):
        return index_code
    return f"sz{index_code}" if index_code.startswith("399") else f"sh{index_code}"


def fetch_index_ohlcv(index_code: str, days: int = 900) -> pd.DataFrame:
    """指数日线 OHLCV。新浪源为主，东财 index_zh_a_hist 兜底（中证系行业指数如
    000930 中证消费新浪缺失或停更）。列: trade_date, open, high, low, close, volume"""
    cols = ["trade_date", "open", "high", "low", "close", "volume"]
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")

    def _window(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
        return df[df["trade_date"] >= cutoff].sort_values("trade_date")

    df = pd.DataFrame()
    try:
        import akshare as ak
        df = _window(ak.stock_zh_index_daily(symbol=_sina_symbol(index_code))
                     .rename(columns={"date": "trade_date"}))
    except Exception as e:  # noqa: BLE001
        logger.warning("新浪指数日线失败（%s）: %s，尝试东财", index_code, e)
    if df.empty:  # 新浪缺失/停更/异常 → 东财兜底
        try:
            import akshare as ak
            df = _window(ak.index_zh_a_hist(symbol=index_code, period="daily")
                         .rename(columns={"日期": "trade_date", "开盘": "open", "收盘": "close",
                                          "最高": "high", "最低": "low", "成交量": "volume"}))
        except Exception as e:  # noqa: BLE001
            logger.warning("采集指数 %s 日线失败: %s", index_code, e)
            return pd.DataFrame(columns=cols)
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            df[col] = None
    return df[cols]


def fetch_index_valuation(index_code: str) -> pd.DataFrame:
    """指数 PE/PB 历史序列（用于分位计算）。多路兜底。

    返回列: trade_date, pe, pb, pe_percentile, pb_percentile（后两列仅蛋卷快照行携带，
    为权威近 10 年分位；历史行恒为 NaN，由 valuation 层按数据充分度择优）。
    """
    name = INDEX_NAME_MAP.get(index_code, INDEX_NAME_MAP["000300"])
    val_cols = ["trade_date", "pe", "pb", "pe_percentile", "pb_percentile"]
    try:
        import akshare as ak
    except ImportError as e:
        logger.error("akshare 未安装: %s", e)
        return pd.DataFrame(columns=val_cols)

    frames: list[pd.DataFrame] = []

    # 路线 1：乐咕 PE + PB（宽基指数支持最好，全历史）
    try:
        pe_df = ak.stock_index_pe_lg(symbol=name)
        pb_df = ak.stock_index_pb_lg(symbol=name)
        pe_df = pe_df.rename(columns={"日期": "trade_date", "静态市盈率": "pe"})[["trade_date", "pe"]]
        pb_df = pb_df.rename(columns={"日期": "trade_date", "市净率": "pb"})[["trade_date", "pb"]]
        merged = pd.merge(pe_df, pb_df, on="trade_date", how="outer").sort_values("trade_date")
        merged["trade_date"] = pd.to_datetime(merged["trade_date"]).dt.strftime("%Y-%m-%d")
        frames.append(merged)
    except Exception as e:  # noqa: BLE001
        logger.warning("乐咕估值接口失败（%s），尝试中证官网估值", e)

    # 路线 2：中证指数官网（覆盖全部中证系行业指数，如 399997 中证白酒，仅近月）
    if not frames:
        try:
            df = ak.stock_zh_index_value_csindex(index_code)
            df = df.rename(columns={"日期": "trade_date", "市盈率1": "pe"})[["trade_date", "pe"]]
            df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
            df["pb"] = None
            df = df.dropna(subset=["pe"]).sort_values("trade_date")
            if not df.empty:
                frames.append(df)
        except Exception as e:  # noqa: BLE001
            logger.warning("中证官网估值接口也失败: %s", e)

    # 路线 3：蛋卷基金指数估值快照（宽基 + 行业，63 个指数，附权威近 10 年分位）。
    # 无论历史路线成败都追加：行业指数 PE 历史不足时，外部分位优于价格分位代理。
    frames.append(_fetch_danjuan_valuation(index_code))

    parts = [f for f in frames if not f.empty]
    if not parts:
        return pd.DataFrame(columns=val_cols)
    out = pd.concat(parts, ignore_index=True)
    for col in val_cols:
        if col not in out.columns:
            out[col] = None
    # 同日去重：蛋卷行（带分位）优先
    out = out.sort_values("trade_date", kind="stable").drop_duplicates(
        "trade_date", keep="last").sort_values("trade_date")
    return out[val_cols]


def _fetch_danjuan_valuation(index_code: str) -> pd.DataFrame:
    """蛋卷基金指数估值：当日 pe/pb + 近 10 年分位（0~1）。失败返回空帧。"""
    import requests

    cols = ["trade_date", "pe", "pb", "pe_percentile", "pb_percentile"]
    try:
        resp = requests.get(
            "https://danjuanfunds.com/djapi/index_eva/dj",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("items", [])
    except Exception as e:  # noqa: BLE001
        logger.warning("蛋卷估值接口失败（%s）: %s", index_code, e)
        return pd.DataFrame(columns=cols)

    # 蛋卷代码带交易所前缀（SZ399997 / SH000300），按后缀匹配
    item = next((x for x in items if str(x.get("index_code", "")).upper().endswith(index_code)), None)
    if item is None or item.get("pe") is None:
        return pd.DataFrame(columns=cols)
    ts = item.get("ts")
    trade_date = (pd.to_datetime(ts, unit="ms", utc=True)
                  .tz_convert("Asia/Shanghai").strftime("%Y-%m-%d") if ts else None)
    if trade_date is None:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame([{
        "trade_date": trade_date,
        "pe": item.get("pe"),
        "pb": item.get("pb"),
        "pe_percentile": round(item["pe_percentile"] * 100, 1) if item.get("pe_percentile") is not None else None,
        "pb_percentile": round(item["pb_percentile"] * 100, 1) if item.get("pb_percentile") is not None else None,
    }])


def fetch_market_news(keyword: str = "股市", max_items: int = 20) -> pd.DataFrame:
    """东财个股/关键词新闻。返回列: published, title, content, source"""
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=keyword)
    except Exception as e:  # noqa: BLE001
        logger.warning("采集新闻失败（关键词 %s）: %s", keyword, e)
        return pd.DataFrame(columns=["published", "title", "content", "source"])
    if df is None or df.empty:
        return pd.DataFrame(columns=["published", "title", "content", "source"])
    df = df.rename(columns={
        "发布时间": "published", "新闻标题": "title", "标题": "title",
        "新闻内容": "content", "内容": "content", "文章来源": "source", "来源": "source",
    })
    if "title" not in df.columns:
        logger.warning("新闻接口列名不匹配: %s", list(df.columns))
        return pd.DataFrame(columns=["published", "title", "content", "source"])
    df["published"] = pd.to_datetime(df.get("published"), errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
    df = df.dropna(subset=["title"]).head(max_items)
    return df[["published", "title", "content", "source"]]
