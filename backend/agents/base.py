"""Agent 基类与统一输出契约。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict


@dataclass
class AgentOutput:
    agent: str
    score: float = 0.0          # -1（强烈看空）~ 1（强烈看多）
    direction: str = "HOLD"     # BUY / SELL / HOLD
    confidence: float = 0.5     # 0 ~ 1
    summary: str = ""
    fallback_used: bool = False # LLM 失败回退量化基线时置 True
    data_quality: float = 1.0   # 0 ~ 1，输入数据充分度（合成层据此降权）
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_log_record(self, trade_date: str, fund_code: str) -> dict:
        import json
        return {
            "trade_date": trade_date,
            "fund_code": fund_code,
            "agent": self.agent,
            "score": self.score,
            "direction": self.direction,
            "confidence": self.confidence,
            "summary": self.summary,
            "fallback_used": int(self.fallback_used),
            "detail_json": json.dumps(self.detail, ensure_ascii=False),
            "data_quality": self.data_quality,
        }


def score_to_direction(score: float, buy: float = 0.25, sell: float = -0.25) -> str:
    if score >= buy:
        return "BUY"
    if score <= sell:
        return "SELL"
    return "HOLD"


class BaseAgent(ABC):
    name: str = "base"

    @abstractmethod
    def analyze(self, ctx: "AnalysisContext") -> AgentOutput: ...


class AnalysisContext(dict):
    """流水线传给各 Agent 的数据上下文：净值历史、因子结果、新闻、指数数据等。"""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(key) from e
