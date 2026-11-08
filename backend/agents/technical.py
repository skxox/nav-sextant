"""技术面 Agent：波动调整动量 + 均线趋势对齐，纯量化，无 LLM。

置信度校准：数据越充分置信度越高；趋势对齐度低（震荡市）时主动降低置信度。
"""
from __future__ import annotations

from .base import AgentOutput, BaseAgent, AnalysisContext, score_to_direction


class TechnicalAgent(BaseAgent):
    name = "technical"

    def analyze(self, ctx: AnalysisContext) -> AgentOutput:
        mf = ctx.momentum
        detail = {
            "window_returns_pct": mf.window_returns,
            "raw_score_pct": mf.raw_score,
            "vol_adj_momentum": mf.vol_adj_momentum,
            "trend_alignment": mf.trend_alignment,
            "above_ma20": mf.above_ma20,
            "above_ma60": mf.above_ma60,
            "ma20_slope_pct": mf.ma20_slope_pct,
        }
        if not mf.window_returns:
            return AgentOutput(agent=self.name, score=0.0, direction="HOLD",
                               confidence=0.2, data_quality=0.2,
                               summary="净值数据不足，动量因子缺失", detail=detail)

        # 数据充分度：60 日窗口全有 → 1.0；仅短窗口 → 0.6
        dq = 1.0 if 60 in mf.window_returns else (0.8 if 20 in mf.window_returns else 0.6)
        # 震荡市（趋势对齐 < 0.7）降低置信度：动量在震荡市可靠性差
        conf = 0.75 if mf.trend_alignment >= 0.7 else 0.55

        trend_desc = "多头" if mf.above_ma20 and mf.above_ma60 else (
            "空头" if not mf.above_ma20 and not mf.above_ma60 else "震荡")
        return AgentOutput(
            agent=self.name,
            score=mf.score,
            direction=score_to_direction(mf.score),
            confidence=conf,
            data_quality=dq,
            summary=f"波动调整动量 {mf.vol_adj_momentum}，趋势{trend_desc}"
                    f"（对齐度 {mf.trend_alignment}；5/20/60 日："
                    f"{mf.window_returns.get(5, 0)}% / {mf.window_returns.get(20, 0)}% / {mf.window_returns.get(60, 0)}%）",
            detail=detail,
        )
