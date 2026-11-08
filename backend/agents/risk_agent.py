"""风险面 Agent：波动率、最大回撤、VaR、下行波动率、波动率制度，纯量化，无 LLM。

输出分数含义：score 越高 = 当前风险越低（越适合持有/加仓）。

专业增强：
- VaR 95%（历史法）：左尾风险度量，比单纯波动率更贴近真实亏损分布
- 下行波动率：只惩罚下跌方向的波动（上涨波动不是风险）
- 波动率制度识别：近 20 日波动 / 长期波动 > 1.5 → 高波动制度，主动降低置信度
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .base import AgentOutput, BaseAgent, AnalysisContext, score_to_direction


class RiskAgent(BaseAgent):
    name = "risk"

    def analyze(self, ctx: AnalysisContext) -> AgentOutput:
        nav_history = ctx.nav_history
        detail: dict = {}
        if nav_history is None or len(nav_history) < 30:
            return AgentOutput(agent=self.name, score=0.0, direction="HOLD",
                               confidence=0.2, data_quality=0.3,
                               summary="净值历史不足 30 日，无法评估风险", detail=detail)

        nav = nav_history.sort_values("trade_date")["nav"].astype(float).to_numpy()
        returns = pd.Series(nav).pct_change().dropna()

        # 年化波动率（近 60 日）
        vol = float(returns.tail(60).std() * np.sqrt(252)) if len(returns) >= 40 else float(returns.std() * np.sqrt(252))
        # 下行波动率（仅负收益）
        neg_returns = returns[returns < 0]
        downside_vol = float(neg_returns.std() * np.sqrt(252)) if len(neg_returns) >= 10 else vol
        # VaR 95%（历史法，日度，转正数表示损失）
        var95_daily = float(-np.percentile(returns, 5)) if len(returns) >= 40 else None
        # 最大回撤（近 120 日）
        window = nav[-120:] if len(nav) >= 120 else nav
        peak = np.maximum.accumulate(window)
        max_dd = float(((window - peak) / peak).min())
        # 近 5 日加速度（短期急跌预警）
        recent_ret = float(nav[-1] / nav[-6] - 1) if len(nav) >= 6 else 0.0
        # 波动率制度：近 20 日波动 / 近 120 日波动
        vol_recent = float(returns.tail(20).std() * np.sqrt(252)) if len(returns) >= 30 else vol
        vol_ratio = vol_recent / vol if vol > 1e-6 else 1.0
        high_regime = vol_ratio >= 1.5

        detail = {
            "annualized_volatility_pct": round(vol * 100, 2),
            "downside_volatility_pct": round(downside_vol * 100, 2),
            "var95_daily_pct": round(var95_daily * 100, 2) if var95_daily is not None else None,
            "max_drawdown_120d_pct": round(max_dd * 100, 2),
            "recent_5d_return_pct": round(recent_ret * 100, 2),
            "vol_regime_ratio": round(vol_ratio, 2),
            "high_vol_regime": bool(high_regime),
        }

        # 打分：波动率越低越好；VaR 左尾惩罚；回撤深 → 中性偏机会；急跌强惩罚
        vol_score = float(np.clip((0.35 - vol) / 0.25, -1, 1))            # 10% 以下低风险，35% 以上高风险
        var_score = float(np.clip((0.03 - (var95_daily or 0.03)) / 0.02, -1, 1)) if var95_daily is not None else 0.0
        dd_score = float(np.clip(-max_dd / 0.30, -1, 1) * 0.5)            # 深回撤 → 中性偏机会
        crash_penalty = float(np.clip(recent_ret / -0.10, 0, 1)) * -0.5   # 一周急跌 >10% 强惩罚
        score = round(float(np.clip(
            vol_score * 0.30 + var_score * 0.20 + dd_score * 0.25 + crash_penalty * 0.25, -1, 1)), 3)

        # 置信度：高波动制度下风险估计本身不可靠 → 降置信
        conf = 0.5 if high_regime else 0.7
        dq = 1.0 if len(nav) >= 120 else (0.8 if len(nav) >= 60 else 0.6)

        return AgentOutput(
            agent=self.name,
            score=score,
            direction=score_to_direction(score),
            confidence=conf,
            data_quality=dq,
            summary=f"年化波动 {detail['annualized_volatility_pct']}%，VaR95 {detail['var95_daily_pct'] or '—'}%/日"
                    f"{'，高波动制度' if high_regime else ''}，120 日最大回撤 {detail['max_drawdown_120d_pct']}%",
            detail=detail,
        )
