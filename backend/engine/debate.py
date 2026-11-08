"""Bull/Bear 多轮辩论 + 风控审核层。

流程（参考 fund-analysis-agents 的辩论-合并范式，扩展为多轮对抗）：
1. Round 1：Bull 立论 → Bear 针对 Bull 论据反驳
2. Round 2..N：双方各自针对对方上一轮论据再反驳/强化（每轮 2 次 LLM 调用）
3. 提前终局：一方置信度 ≥0.85 且对方 ≤0.25 → 压倒性优势，停止后续轮次
4. Judge 看完整辩论记录后仲裁：同意信号 / 否决信号

确定性约束（代码强制，LLM 无权越过）：
- 仲裁只能让信号"更保守"（BUY→HOLD / SELL→HOLD），不能更激进
- 任何一轮 LLM 失败 → 辩论层整体降级，保留原信号，不阻塞流水线
- 轮数由 config.debate.rounds 控制（1~4），成本 = 2×轮数 + 1 次调用
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..config import DebateSettings, LLMSettings
from ..llm.client import LLMClient, LLMError
from .orchestrator import CompositeSignal

logger = logging.getLogger(__name__)

BULL_SYSTEM = """你是辩论中的多方（Bull）。给定一个基金操作信号和多维 Agent 分析摘要，
从消息面、估值、资金面寻找支持该信号的最强论据。
若有空方上一轮的反驳论据，你必须逐条正面回应（认同有理的部分可以承认，但需给出为何不改变结论）。
严格输出 JSON：
{"points": ["论据1", "论据2", "论据3"], "confidence": 0.7}
confidence 为你对信号方向的置信度 0~1。不要输出 JSON 以外内容；论据必须基于给定信息，不得编造数据。"""

BEAR_SYSTEM = """你是辩论中的空方（Bear）。给定一个基金操作信号和多维 Agent 分析摘要，
尽力寻找反对该信号的风险与反例（估值陷阱、动量反转、流动性、事件风险）。
若有多方上一轮的论据，你必须逐条反驳，指出其薄弱处或被忽略的反例。
严格输出 JSON：
{"points": ["反驳论据1", "反驳论据2", "反驳论据3"], "confidence": 0.7}
confidence 为你反对该信号的置信度 0~1。不要输出 JSON 以外内容；论据必须基于给定信息，不得编造数据。"""

JUDGE_SYSTEM = """你是风控审核官，仲裁 Bull 与 Bear 的多轮辩论并给出最终裁定。
你将看到完整的逐轮辩论记录，重点评估：后一轮是否有效回应了前一轮的反驳、
论据是否基于给定数据而非空泛推断、置信度变化趋势。
背景规则：你只能让信号更保守（BUY→HOLD / SELL→HOLD），不能更激进。
严格输出 JSON：
{"agree": <bool, 是否维持原信号>, "confidence": <0~1>, "reasoning": "<120字内裁定理由>"}
拿不准时倾向否决（agree=false），宁可错过不可做错。"""


@dataclass
class DebateRound:
    round_no: int
    bull_points: list[str] = field(default_factory=list)
    bear_points: list[str] = field(default_factory=list)
    bull_confidence: float = 0.5
    bear_confidence: float = 0.5

    def to_dict(self) -> dict:
        return {
            "round": self.round_no,
            "bull_points": self.bull_points,
            "bear_points": self.bear_points,
            "bull_confidence": self.bull_confidence,
            "bear_confidence": self.bear_confidence,
        }


@dataclass
class DebateResult:
    triggered: bool = False
    agree: bool = True
    final_action: str = "HOLD"
    confidence: float = 0.5
    bull_points: list[str] = field(default_factory=list)   # 最后一轮多方论据（向后兼容）
    bear_points: list[str] = field(default_factory=list)   # 最后一轮空方论据
    rounds: list[dict] = field(default_factory=list)       # 完整逐轮记录
    rounds_used: int = 0
    judge_reasoning: str = ""
    unavailable_reason: str = ""


def _conservative(action: str, agree: bool) -> str:
    """风控审核的确定性约束：仲裁只能向 HOLD 收敛。"""
    return action if agree else "HOLD"


def _blowout(round_: DebateRound, direction: str) -> bool:
    """压倒性优势判定（提前终局）。direction 为原信号方向。"""
    if direction == "BUY":
        return round_.bull_confidence >= 0.85 and round_.bear_confidence <= 0.25
    return round_.bear_confidence >= 0.85 and round_.bull_confidence <= 0.25


def _transcript(rounds: list[DebateRound]) -> str:
    lines = []
    for r in rounds:
        lines.append(f"【第 {r.round_no} 轮】")
        lines.append(f"Bull（置信 {r.bull_confidence:.2f}）：{json.dumps(r.bull_points, ensure_ascii=False)}")
        lines.append(f"Bear（置信 {r.bear_confidence:.2f}）：{json.dumps(r.bear_points, ensure_ascii=False)}")
    return "\n".join(lines)


def run_debate(llm: LLMClient, llm_settings: LLMSettings,
               signal: CompositeSignal, debate_settings: DebateSettings | None = None) -> DebateResult:
    cfg = debate_settings or DebateSettings()
    result = DebateResult(triggered=True, final_action=signal.action)
    if signal.action == "HOLD":
        return DebateResult(triggered=False, final_action=signal.action)
    if not llm_settings.debate_enabled:
        return DebateResult(triggered=False, final_action=signal.action,
                            unavailable_reason="debate_disabled")

    context = (
        f"基金：{signal.fund_name}（{signal.fund_code}）\n"
        f"初步信号：{signal.action}，综合分 {signal.composite_score:+.3f}，建议仓位 {signal.target_position:.0%}\n"
        f"多维 Agent 分析：\n" + "\n".join(f"- [{k}] {v.summary}" for k, v in signal.agent_outputs.items() if v.summary)
    )
    try:
        rounds: list[DebateRound] = []
        for r in range(1, cfg.rounds + 1):
            cur = DebateRound(round_no=r)
            # Bull：第 1 轮立论；之后针对 Bear 上轮反驳
            bull_rebut = (f"\n空方上一轮反驳论据：\n{json.dumps(rounds[-1].bear_points, ensure_ascii=False)}"
                          if rounds else "")
            bull = llm.chat_json(BULL_SYSTEM, f"{context}{bull_rebut}\n\n这是第 {r} 轮辩论。")
            cur.bull_points = [str(p) for p in bull.get("points", [])][:3]
            cur.bull_confidence = min(max(float(bull.get("confidence", 0.5)), 0.0), 1.0)
            # Bear：始终针对 Bull 本轮论据反驳
            bear = llm.chat_json(BEAR_SYSTEM,
                                 f"{context}\n\n多方本轮论据：\n{json.dumps(cur.bull_points, ensure_ascii=False)}"
                                 f"\n\n这是第 {r} 轮辩论。")
            cur.bear_points = [str(p) for p in bear.get("points", [])][:3]
            cur.bear_confidence = min(max(float(bear.get("confidence", 0.5)), 0.0), 1.0)
            rounds.append(cur)
            result.rounds = [x.to_dict() for x in rounds]
            result.rounds_used = r
            result.bull_points = cur.bull_points
            result.bear_points = cur.bear_points
            if cfg.consensus_early_stop and _blowout(cur, signal.action):
                logger.info("基金 %s 第 %d 轮出现压倒性优势，提前终局", signal.fund_code, r)
                break

        judge = llm.chat_json(JUDGE_SYSTEM, f"{context}\n\n完整辩论记录：\n{_transcript(rounds)}")
        result.agree = bool(judge.get("agree", True))
        result.confidence = min(max(float(judge.get("confidence", 0.5)), 0.0), 1.0)
        result.judge_reasoning = str(judge.get("reasoning", ""))
        result.final_action = _conservative(signal.action, result.agree)
    except (LLMError, KeyError, ValueError, TypeError) as e:
        logger.warning("基金 %s 辩论层不可用: %s", signal.fund_code, e)
        result.unavailable_reason = str(e)
        result.final_action = signal.action  # fail-closed：保留原信号
    return result
