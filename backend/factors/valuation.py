"""估值因子（专业版）：PE/PB 历史分位 + 股权风险溢价（ERP）+ 估值分位动量。

三个正交维度合成：
1. 分位水平：当前 PE/PB 在历史窗口的分位（绝对贵贱）
2. ERP 股债性价比：盈利收益率 1/PE − 无风险利率（跨资产比较，格雷厄姆框架）
3. 分位动量：分位近 20 日的变动（估值扩张/收缩方向，抢先反映资金态度）
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

DEFAULT_RISK_FREE = 0.018  # 无风险利率年化（10 年期国债近似），ERP 缺省值


@dataclass
class ValuationFactor:
    current_pe: float | None = None
    current_pb: float | None = None
    pe_percentile: float | None = None      # 0 ~ 100
    pb_percentile: float | None = None      # 0 ~ 100
    percentile_change_20d: float | None = None  # 分位 20 日变化（百分点，正=扩张变贵）
    earnings_yield_pct: float | None = None     # 1/PE × 100
    erp_pct: float | None = None                # 股权风险溢价（盈利收益率 − 无风险利率）
    erp_percentile: float | None = None         # ERP 历史分位（越高越有吸引力）
    price_proxy: bool = False                   # PE 历史不足时用指数价格分位代理
    percentile_source: str = "none"             # history=自有历史 / external=外部快照(蛋卷10年) / proxy / none
    score: float = 0.0                          # -1 ~ 1，越高越被低估

    @property
    def data_quality(self) -> float:
        """数据充分度：PE+PB+ERP 齐全 1.0，仅 PE 0.6，价格代理 0.45。"""
        if self.price_proxy:
            return 0.45
        q = 0.4
        if self.pe_percentile is not None:
            q += 0.3
        if self.pb_percentile is not None:
            q += 0.15
        if self.erp_percentile is not None:
            q += 0.15
        return q


def _percentile(series: pd.Series, value: float) -> float:
    return float((series < value).mean() * 100)


def _latest_external_row(index_valuation: pd.DataFrame) -> dict | None:
    """最新一行携带的外部权威分位（蛋卷快照）；无则 None。"""
    for col in ("pe_percentile", "pb_percentile"):
        if col not in index_valuation.columns:
            return None
    rows = index_valuation.dropna(subset=["pe_percentile"]).sort_values("trade_date")
    if rows.empty:
        return None
    return rows.iloc[-1].to_dict()


def _compose_score(vf: ValuationFactor) -> None:
    parts: list[float] = []
    weights: list[float] = []
    if vf.pe_percentile is not None:
        parts.append((50 - vf.pe_percentile) / 50)
        weights.append(1.0)
    if vf.pb_percentile is not None:
        parts.append((50 - vf.pb_percentile) / 50)
        weights.append(0.6)
    if vf.erp_percentile is not None:
        parts.append((vf.erp_percentile - 50) / 50)  # ERP 分位越高分越高
        weights.append(0.8)
    if vf.percentile_change_20d is not None:
        # 分位快速扩张（变贵）→ 减分；快速收缩 → 加分
        parts.append(float(np.clip(-vf.percentile_change_20d / 20, -0.5, 0.5)))
        weights.append(0.4)
    vf.score = round(float(np.average(parts, weights=weights)), 3) if parts else 0.0


def compute_valuation(index_valuation: pd.DataFrame,
                      window: int = 750,
                      risk_free_rate: float = DEFAULT_RISK_FREE) -> ValuationFactor:
    """估值因子。分位来源三级降级：

    1. history  —— 自有 PE 历史行数 ≥ 60，窗口内自分位（ERP/分位动量可用）
    2. external —— 采集层写入的外部权威分位快照（蛋卷，近 10 年窗口），行业指数
                   PE 历史不足时的最优解；随流水线日积月累行数够 60 后自动回一级
    3. proxy    —— 指数价格分位代理（PE 与价格长期相关，精度最低）
    """
    vf = ValuationFactor()
    if index_valuation is None or index_valuation.empty:
        vf.percentile_source = "none"
        return vf

    df = index_valuation.dropna(subset=["pe"]).sort_values("trade_date").tail(window)

    if len(df) >= 60:
        vf.percentile_source = "history"
    else:
        ext = _latest_external_row(index_valuation)
        if ext is not None and ext.get("pe") is not None:
            # ---- 二级：外部权威分位（蛋卷近 10 年）----
            vf.percentile_source = "external"
            vf.current_pe = round(float(ext["pe"]), 2)
            vf.pe_percentile = round(float(ext["pe_percentile"]), 1)
            if ext.get("pb") is not None:
                vf.current_pb = round(float(ext["pb"]), 2)
            if ext.get("pb_percentile") is not None:
                vf.pb_percentile = round(float(ext["pb_percentile"]), 1)
            if vf.current_pe and vf.current_pe > 0:
                vf.earnings_yield_pct = round(100.0 / vf.current_pe, 2)
                vf.erp_pct = round(vf.earnings_yield_pct - risk_free_rate * 100, 2)
            _compose_score(vf)
            return vf
        # ---- 三级：价格分位代理 ----
        if "close" in index_valuation.columns:
            close_df = index_valuation.dropna(subset=["close"]).sort_values("trade_date").tail(window)
            if len(close_df) >= 60:
                # 只取 close 列再改名，避免与已有的 pe 列重名（重复列名会让取值变成 Series）
                df = close_df[["trade_date", "close"]].rename(columns={"close": "pe"})
                vf.price_proxy = True
                vf.percentile_source = "proxy"

    if df.empty:
        vf.percentile_source = "none"
        _compose_score(vf)
        return vf
    vf.current_pe = round(float(df.iloc[-1]["pe"]), 2)
    vf.pe_percentile = round(_percentile(df["pe"], vf.current_pe), 1)

    pb_series = index_valuation.dropna(subset=["pb"]).sort_values("trade_date").tail(window)
    if not pb_series.empty:
        vf.current_pb = round(float(pb_series.iloc[-1]["pb"]), 2)
        vf.pb_percentile = round(_percentile(pb_series["pb"], vf.current_pb), 1)

    # ---- ERP 股债性价比（价格代理模式下 1/价格 无意义，跳过） ----
    if not vf.price_proxy and vf.current_pe and vf.current_pe > 0:
        vf.earnings_yield_pct = round(100.0 / vf.current_pe, 2)
        vf.erp_pct = round(vf.earnings_yield_pct - risk_free_rate * 100, 2)
        erp_series = 100.0 / df["pe"].clip(lower=0.1) - risk_free_rate * 100
        vf.erp_percentile = round(float((erp_series < vf.erp_pct).mean() * 100), 1)

    # ---- 分位动量：PE 分位 20 日变化 ----
    if len(df) >= 40:
        hist_pct = [_percentile(df["pe"].iloc[: i + 1], df["pe"].iloc[i]) for i in range(len(df))]
        now_pct, past_pct = hist_pct[-1], hist_pct[-21] if len(hist_pct) >= 21 else hist_pct[0]
        vf.percentile_change_20d = round(now_pct - past_pct, 1)

    _compose_score(vf)
    return vf
