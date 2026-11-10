"""编排层：聚合多维 Agent 契约输出，加权合成初步信号与仓位建议。

编排层只认 AgentOutput 契约字段。专业合成机制：

1. 三重加权：配置权重 × 置信度 × 数据质量 —— 低置信、数据不足的 Agent 影响趋零
   （有效权重 = w × max(conf, 0.05)/0.5 × data_quality）。
2. 分歧检测（Disagreement Guard）：任意两个 Agent 出现方向相反的强观点
   （异号且 |score| ≥ 0.3）时，多空信号可靠性差 → 阈值临时收紧 1.5 倍，
   减少在分歧市况下的频繁交易（借鉴 ensemble 投票的 unanimity 收紧思路）。
3. 仓位映射：信号强度线性映射到 [0, max_single_fund_position]。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..agents.base import AgentOutput
from ..config import Settings


@dataclass
class CompositeSignal:
    fund_code: str
    fund_name: str
    trade_date: str
    composite_score: float = 0.0
    action: str = "HOLD"
    target_position: float = 0.0   # 建议目标仓位（占组合比例，风控前）
    disagreement: bool = False     # Agent 间存在方向相反的强观点
    agent_outputs: dict[str, AgentOutput] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def _detect_disagreement(outputs: dict[str, AgentOutput]) -> bool:
    """任意两个 Agent 异号且均为强观点（|score| ≥ 0.3）→ 分歧。"""
    strong = [o.score for o in outputs.values() if abs(o.score) >= 0.3]
    if len(strong) < 2:
        return False
    return bool(np.any(np.array(strong) > 0) and np.any(np.array(strong) < 0))


def synthesize(fund_code: str, fund_name: str, trade_date: str,
               outputs: dict[str, AgentOutput], settings: Settings,
               ic_adjustments: dict | None = None) -> CompositeSignal:
    """ic_adjustments: {agent: ICAdjustment.to_dict()}，因子 IC 检验驱动的权重修正。"""
    weights: dict[str, float] = settings.weights.model_dump()
    ic_adjustments = ic_adjustments or {}
    num, den = 0.0, 0.0
    for name, out in outputs.items():
        w = weights.get(name, 0.0)
        if w <= 0:
            continue
        # 三重加权：置信度（0.5 为基准）× 数据质量；再乘 IC 调权系数
        adj = ic_adjustments.get(name)
        mult = float(adj.get("mult", 1.0)) if adj else 1.0
        sign = int(adj.get("sign", 1)) if adj else 1
        eff_w = w * max(out.confidence, 0.05) / 0.5 * float(np.clip(out.data_quality, 0.0, 1.0)) * mult
        num += eff_w * out.score * sign
        den += eff_w

    score = round(num / den, 3) if den > 0 else 0.0

    # 分歧检测：阈值收紧 1.5 倍（保守方向）
    disagreement = _detect_disagreement(outputs)
    buy_th = settings.thresholds.buy * (1.5 if disagreement else 1.0)
    sell_th = settings.thresholds.sell * (1.5 if disagreement else 1.0)

    action = "HOLD"
    if score >= buy_th:
        action = "BUY"
    elif score <= sell_th:
        action = "SELL"

    # 仓位建议：信号强度线性映射，clip 到 [0, max_single_fund_position]
    # （修复：未 clip 时 composite > 0.5 的强信号 target 超上限，必被单基仓位风控拦截）
    strength = abs(score)
    target = round(min(settings.max_single_fund_position * strength * 2,
                      settings.max_single_fund_position), 3) if action != "HOLD" else 0.0

    reasons = [f"[{o.agent}] {o.summary}" for o in outputs.values() if o.summary]
    if disagreement:
        reasons.append("[orchestrator] 检测到多空 Agent 强分歧，买入/卖出阈值已临时收紧 1.5 倍")
    for name, adj in ic_adjustments.items():
        if adj.get("note"):
            reasons.append(f"[ic] {adj['note']}")
    return CompositeSignal(
        fund_code=fund_code, fund_name=fund_name, trade_date=trade_date,
        composite_score=score, action=action, target_position=target,
        disagreement=disagreement, agent_outputs=outputs, reasons=reasons,
    )
