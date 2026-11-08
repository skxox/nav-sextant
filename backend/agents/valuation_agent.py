"""估值面 Agent：PE/PB 分位 + ERP 股债性价比 + 分位动量，纯量化，无 LLM。"""
from __future__ import annotations

from .base import AgentOutput, BaseAgent, AnalysisContext, score_to_direction


class ValuationAgent(BaseAgent):
    name = "valuation"

    def analyze(self, ctx: AnalysisContext) -> AgentOutput:
        vf = ctx.valuation
        detail = {
            "current_pe": vf.current_pe, "pe_percentile": vf.pe_percentile,
            "current_pb": vf.current_pb, "pb_percentile": vf.pb_percentile,
            "earnings_yield_pct": vf.earnings_yield_pct,
            "erp_pct": vf.erp_pct, "erp_percentile": vf.erp_percentile,
            "percentile_change_20d": vf.percentile_change_20d,
            "percentile_source": vf.percentile_source,
        }
        if vf.pe_percentile is None:
            return AgentOutput(agent=self.name, score=0.0, direction="HOLD",
                               confidence=0.2, data_quality=0.3,
                               summary="估值数据缺失", detail=detail)

        dq = vf.data_quality
        src_note = {"proxy": "（价格分位代理）", "external": "（蛋卷近10年外部分位）"}.get(
            vf.percentile_source, "")
        parts = [f"PE 分位 {vf.pe_percentile}%{src_note}"]
        if vf.erp_percentile is not None:
            parts.append(f"ERP 分位 {vf.erp_percentile}%（股债性价比）")
        if vf.percentile_change_20d is not None:
            direction = "扩张" if vf.percentile_change_20d > 0 else "收缩"
            parts.append(f"估值{direction} {abs(vf.percentile_change_20d)}pt/20日")
        return AgentOutput(
            agent=self.name,
            score=vf.score,
            direction=score_to_direction(vf.score),
            confidence=0.75,
            data_quality=dq,
            summary="，".join(parts),
            detail=detail,
        )
