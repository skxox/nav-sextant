"""消息面 Agent（LLM 工程专业版）。

参照 TradingAgents / FinAgent 的 LLM 分析层设计，四项核心机制：

1. 证据注入（Evidence-Grounded）：除新闻外，同时注入量化三因子摘要
   （动量/估值/风险），要求 LLM 交叉验证 —— 新闻观点与数据事实矛盾时保守处理，
   杜绝 LLM 脱离数据的自由发挥。
2. Self-Consistency 多采样：同一证据独立采样 N 次（temperature>0），
   score 取中位数，消除单次输出的随机性；样本极差过大时自动降低置信度。
3. 结构化校验（pydantic）：输出走 SentimentVerdict 强校验，
   非法输出自动重试一次，仍失败才回退 fail-closed。
4. 置信度校准：confidence = 采样一致度 × 平均置信度 × 数据充分度
   （新闻 < 3 条时上限压到 0.6），宁可表达不确定性也不虚报把握。

fail-closed：LLM 全部失败时回退中性评分并标记 fallback，不阻塞流水线。
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime

import numpy as np
from pydantic import BaseModel, Field, ValidationError

from .base import AgentOutput, BaseAgent, AnalysisContext, score_to_direction
from ..llm.client import LLMClient, LLMError

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一名严谨的公募基金消息面分析师，任务是基于新闻与量化数据评估消息面对该基金未来 1-4 周的影响。

## 输出要求
严格输出 JSON（不要输出其他任何内容）：
{
  "score": <float, -1到1，消息面综合利好程度，0为中性>,
  "confidence": <float, 0到1, 你对本判断的把握>,
  "key_events": [<string, 最重要的2-3条事件，每条格式: "日期|事件一句话|利好/利空/中性">],
  "consistency_check": "<string, 一句话：新闻观点与量化数据是否一致？矛盾时你如何取舍>",
  "reasoning": "<string, 不超过100字的判断依据，必须引用至少一条新闻或数据>"
}

## 分析纪律（违反任何一条即视为不合格输出）
1. 只依据提供的新闻和数据，禁止使用未提及的信息或你的记忆
2. 每条 key_event 必须来自给定新闻原文，不得改写事实
3. 新闻观点与量化数据明显矛盾时（如新闻利好但动量为深度负值），score 向 0 收缩并在 consistency_check 中说明
4. 新闻为空、只有标题无实质内容、或全部为旧闻（>7天）时：score 接近 0，confidence ≤ 0.3
5. 模糊消息宁可保守：拿不准就向 0 收缩"""


class SentimentVerdict(BaseModel):
    """消息面单次采样输出的强校验契约。"""
    score: float = Field(ge=-1.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    key_events: list[str] = Field(default_factory=list, max_length=5)
    consistency_check: str = ""
    reasoning: str = ""


def _quant_evidence(ctx: AnalysisContext) -> list[str]:
    """把量化三因子打包成 LLM 可读的证据行（交叉验证的锚点）。"""
    lines: list[str] = []
    mf = ctx.momentum
    if mf and mf.window_returns:
        lines.append(f"量化动量: 5/20/60日收益 {mf.window_returns.get(5, 0)}%/"
                     f"{mf.window_returns.get(20, 0)}%/{mf.window_returns.get(60, 0)}%，"
                     f"趋势对齐度 {mf.trend_alignment}")
    vf = ctx.valuation
    if vf and vf.pe_percentile is not None:
        lines.append(f"量化估值: PE分位 {vf.pe_percentile}%"
                     + (f"，ERP分位 {vf.erp_percentile}%" if vf.erp_percentile is not None else ""))
    nav = ctx.nav_history
    if nav is not None and len(nav) >= 20:
        import pandas as pd
        r = pd.Series(nav["nav"].astype(float)).pct_change().dropna().tail(20)
        lines.append(f"量化风险: 近20日实现波动 {float(r.std() * np.sqrt(252)) * 100:.1f}%（年化），"
                     f"近5日 {float(nav['nav'].iloc[-1] / nav['nav'].iloc[-6] - 1) * 100:.1f}%")
    return lines


class SentimentAgent(BaseAgent):
    name = "sentiment"

    def __init__(self, llm: LLMClient | None = None, fallback_enabled: bool = True,
                 samples: int = 3):
        self.llm = llm
        self.fallback_enabled = fallback_enabled
        self.samples = max(1, samples)

    # ---- 单次采样（含校验重试；不缓存——采样需要多样性） ----
    def _sample_once(self, user_prompt: str) -> SentimentVerdict:
        last_err: Exception | None = None
        for attempt in (1, 2):
            try:
                raw = self.llm.chat_json(SYSTEM_PROMPT, user_prompt,
                                         temperature=0.7, use_cache=False)
                return SentimentVerdict.model_validate(raw)
            except (LLMError, ValidationError, ValueError, TypeError) as e:
                last_err = e
                logger.warning("消息面采样第 %d 次校验失败: %s", attempt, e)
        raise LLMError(f"采样输出连续非法: {last_err}")

    def analyze(self, ctx: AnalysisContext) -> AgentOutput:
        news = ctx.news or []
        fund_name = ctx.get("fund_name", ctx.get("fund_code", ""))

        # 数据充分度：有效新闻条数决定置信度上限
        news_count = len(news)
        conf_cap = 0.85 if news_count >= 5 else (0.6 if news_count >= 3 else 0.35)

        if not news:
            return AgentOutput(agent=self.name, score=0.0, direction="HOLD", confidence=0.1,
                               data_quality=0.2, summary="无可用新闻，保持中性", fallback_used=True)

        now = datetime.now()
        news_text = "\n".join(
            f"- [{item.get('published', '')}] {item.get('title', '')}：{(item.get('content') or '')[:120]}"
            for item in news[:15]
        )
        evidence = "\n".join(_quant_evidence(ctx)) or "（量化数据不足，无法提供）"
        user_prompt = (
            f"基金：{fund_name}\n"
            f"分析时点：{now:%Y-%m-%d}（早于该日期 7 天以上的新闻视为旧闻）\n\n"
            f"## 量化数据（交叉验证锚点）\n{evidence}\n\n"
            f"## 近期新闻（共 {news_count} 条）\n{news_text}\n\n"
            f"请按系统指令输出 JSON 评估。"
        )

        if self.llm is None:
            return self._fallback("LLM 未配置")

        # ---- self-consistency：N 次独立采样 ----
        try:
            verdicts: list[SentimentVerdict] = []
            for _ in range(self.samples):
                verdicts.append(self._sample_once(user_prompt))
        except LLMError as e:
            logger.warning("消息面 Agent 采样失败: %s", e)
            return self._fallback(str(e))

        scores = [v.score for v in verdicts]
        median_score = float(statistics.median(scores))
        spread = max(scores) - min(scores)
        mean_conf = float(statistics.mean(v.confidence for v in verdicts))
        # 置信度 = 一致度(极差惩罚) × 采样平均置信度 × 数据充分度上限
        agreement = float(np.clip(1.0 - spread / 1.2, 0.3, 1.0))
        confidence = round(min(mean_conf * agreement, conf_cap), 3)

        # 聚合事件与推理（取中位样本的表述，附样本分歧供审计）
        median_verdict = min(verdicts, key=lambda v: abs(v.score - median_score))
        detail = {
            "reasoning": median_verdict.reasoning,
            "key_events": median_verdict.key_events[:3],
            "consistency_check": median_verdict.consistency_check,
            "samples": {"n": len(verdicts), "scores": scores, "spread": round(spread, 3),
                        "agreement": round(agreement, 3), "confidence_cap_by_data": conf_cap},
        }
        summary = "；".join(median_verdict.key_events[:2]) or median_verdict.reasoning
        if spread > 0.8:
            summary += "（注：多次采样分歧较大，请谨慎参考）"

        return AgentOutput(
            agent=self.name,
            score=round(median_score, 3),
            direction=score_to_direction(median_score),
            confidence=confidence,
            data_quality=0.6 if news_count < 3 else (0.8 if news_count < 8 else 1.0),
            summary=summary,
            detail=detail,
        )

    def _fallback(self, reason: str) -> AgentOutput:
        if not self.fallback_enabled:
            raise LLMError(f"消息面 Agent 失败且未启用回退: {reason}")
        return AgentOutput(
            agent=self.name, score=0.0, direction="HOLD", confidence=0.1,
            data_quality=0.2, summary=f"LLM 不可用（{reason}），回退中性评分",
            fallback_used=True, detail={"error": reason},
        )
