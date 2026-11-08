"""跨层共享的 pydantic 契约模型（与前端 src/api/types.ts 对齐）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AgentName = Literal["technical", "valuation", "sentiment", "risk", "debate"]
SignalAction = Literal["BUY", "SELL", "HOLD"]


class RiskCheckModel(BaseModel):
    name: str
    passed: bool
    detail: str


class AgentOutputModel(BaseModel):
    agent: AgentName
    score: float = Field(..., ge=-1, le=1)
    direction: SignalAction = "HOLD"
    confidence: float = Field(default=0.5, ge=0, le=1)
    summary: str = ""
    fallback_used: bool = False
    data_quality: float = Field(default=1.0, ge=0, le=1)
    detail: dict = Field(default_factory=dict)


class DebateResultModel(BaseModel):
    """Bull/Bear 辩论 + 风控审核层输出。"""
    triggered: bool = False
    agree: bool = True
    final_action: SignalAction = "HOLD"
    confidence: float = 0.5
    bull_points: list[str] = Field(default_factory=list)
    bear_points: list[str] = Field(default_factory=list)
    rounds: list[dict] = Field(default_factory=list, description="多轮辩论完整记录")
    rounds_used: int = 0
    judge_reasoning: str = ""
    unavailable_reason: str = ""  # LLM 不可用时的原因


class SignalModel(BaseModel):
    trade_date: str
    fund_code: str
    fund_name: str
    action: SignalAction
    target_position: float = Field(..., ge=0, le=1)
    composite_score: float = Field(..., ge=-1, le=1)
    risk_passed: bool
    risk_summary: str
    risk_checks: list[RiskCheckModel] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    agents: dict[str, AgentOutputModel] = Field(default_factory=dict)
    debate: DebateResultModel = Field(default_factory=DebateResultModel)
    nav_estimate: dict | None = None
