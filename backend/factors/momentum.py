"""多周期动量因子（专业版）：多窗口收益 + 波动调整 + 均线趋势对齐。

合成逻辑（借鉴量化多因子框架的正交合成思路）：
1. 多窗口加权收益率（原始动量）
2. 波动调整：动量 / 年化波动率 → 类夏普式动量质量（同样涨幅，波动小的更可信）
3. 趋势对齐：净值相对 MA20/MA60 的位置与均线斜率 → 单边趋势中动量可信度高，
   震荡市中动量信号衰减（whipsaw 惩罚）

最终 score = tanh(波动调整动量) × 趋势对齐系数
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class MomentumFactor:
    window_returns: dict[int, float] = field(default_factory=dict)
    raw_score: float = 0.0              # 加权收益率（%，未归一）
    annualized_vol: float | None = None # 用于波动调整的年化波动率
    vol_adj_momentum: float = 0.0       # 波动调整后动量（类夏普比率量纲）
    trend_alignment: float = 1.0        # 0 ~ 1，均线趋势对齐系数（震荡市衰减）
    above_ma20: bool | None = None
    above_ma60: bool | None = None
    ma20_slope_pct: float | None = None # MA20 十日斜率（年化近似）
    score: float = 0.0                  # 归一化到 -1 ~ 1


def _trend_alignment(nav: np.ndarray) -> tuple[float, dict]:
    """均线趋势对齐：价格位置 × 均线斜率 × 均线间关系，震荡市惩罚。"""
    info: dict = {}
    if len(nav) < 60:
        return 1.0, info  # 数据不足不惩罚，由 data_quality 控制

    price = nav[-1]
    ma20 = float(nav[-20:].mean())
    ma60 = float(nav[-60:].mean())
    ma20_prev = float(nav[-30:-10].mean())
    above20, above60 = price >= ma20, price >= ma60
    ma20_slope = (ma20 / ma20_prev - 1) if ma20_prev else 0.0  # 十日 MA20 斜率

    # 位置分：价格在均线上的数量（0/1/2）
    pos_score = float(above20) + float(above60)
    # 斜率分：MA20 上行 +1、下行 -1（单边趋势识别）
    slope_score = float(np.clip(ma20_slope * 100, -1, 1))
    # 多头/空头排列：MA20 与 MA60 相对位置
    arrangement = 1.0 if ma20 >= ma60 else -1.0

    info = {
        "above_ma20": bool(above20), "above_ma60": bool(above60),
        "ma20_slope_pct_10d": round(ma20_slope * 100, 3),
        "bullish_arrangement": bool(ma20 >= ma60),
    }
    # 对齐系数 0.5 ~ 1.0：完全多头排列 → 1.0，完全矛盾 → 0.5（动量信号减半）
    alignment = 0.5 + 0.5 * ((pos_score / 2) * 0.4 + slope_score * 0.3 + arrangement * 0.3) / 1.0
    return float(np.clip(alignment, 0.4, 1.0)), info


def compute_momentum(nav_history: pd.DataFrame,
                     windows: list[int] = (5, 20, 60),
                     window_weights: list[float] = (0.2, 0.3, 0.5)) -> MomentumFactor:
    mf = MomentumFactor()
    if nav_history is None or len(nav_history) < min(windows) + 1:
        return mf

    nav = nav_history.sort_values("trade_date")["nav"].astype(float).to_numpy()
    weights = np.array(window_weights[: len(windows)], dtype=float)
    weights = weights / weights.sum()

    total = 0.0
    for w, weight in zip(windows, weights):
        if len(nav) <= w:
            continue
        ret = nav[-1] / nav[-1 - w] - 1
        mf.window_returns[w] = round(float(ret) * 100, 3)
        total += weight * ret

    mf.raw_score = round(float(total) * 100, 3)

    # 波动调整：动量质量 = 加权收益 / 年化波动（上限截断防止低波动爆分）
    returns = pd.Series(nav).pct_change().dropna()
    if len(returns) >= 20:
        vol = float(returns.tail(60).std() * np.sqrt(252)) if len(returns) >= 40 else float(returns.std() * np.sqrt(252))
        mf.annualized_vol = round(vol, 4)
        daily_equiv = vol / np.sqrt(252)
        mf.vol_adj_momentum = round(float(np.clip(total / max(daily_equiv, 1e-6), -3, 3)), 3)

    # 趋势对齐
    alignment, info = _trend_alignment(nav)
    mf.trend_alignment = round(alignment, 3)
    mf.above_ma20 = info.get("above_ma20")
    mf.above_ma60 = info.get("above_ma60")
    mf.ma20_slope_pct = info.get("ma20_slope_pct_10d")

    # 合成：tanh(波动调整动量 × 2) × 趋势对齐
    base = mf.vol_adj_momentum if mf.vol_adj_momentum != 0 else float(np.tanh(total * 100 / 5.0))
    mf.score = round(float(np.tanh(base * 2.0) * mf.trend_alignment), 3)
    return mf
