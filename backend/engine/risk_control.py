"""硬风控前置检查：确定性规则，任何信号（含 LLM 主张）必须通过。

fail-closed 原则：检查所需数据缺失时按不通过处理，宁可不交易。
"""
from __future__ import annotations

from dataclasses import dataclass

from .orchestrator import CompositeSignal
from ..config import Settings


@dataclass
class RiskCheckResult:
    passed: bool
    checks: list[dict]

    @property
    def summary(self) -> str:
        failed = [c["name"] for c in self.checks if not c["passed"]]
        return "通过" if not failed else f"拦截：{'、'.join(failed)}"


def check(signal: CompositeSignal, settings: Settings,
          current_position: float | None, portfolio_drawdown: float | None) -> RiskCheckResult:
    checks: list[dict] = []

    # 1. 单基金仓位上限
    if signal.action == "BUY":
        ok = signal.target_position <= settings.max_single_fund_position + 1e-9
        checks.append({
            "name": "单基仓位上限",
            "passed": bool(ok),
            "detail": f"建议仓位 {signal.target_position:.0%} ≤ 上限 {settings.max_single_fund_position:.0%}",
        })

    # 2. 组合回撤限制：回撤超限时禁止加仓
    if signal.action == "BUY" and portfolio_drawdown is not None:
        ok = abs(portfolio_drawdown) < settings.max_drawdown
        checks.append({
            "name": "组合最大回撤",
            "passed": bool(ok),
            "detail": f"当前回撤 {portfolio_drawdown:.1%}，阈值 {settings.max_drawdown:.0%}，"
                      f"{'允许' if ok else '回撤超限禁止加仓'}",
        })

    # 3. 现金缓冲：数据缺失按不通过
    if signal.action == "BUY":
        if current_position is None:
            checks.append({"name": "现金缓冲", "passed": False, "detail": "当前仓位数据缺失，fail-closed 拦截"})
        else:
            ok = current_position + signal.target_position <= 1.0 - settings.cash_buffer + 1e-9
            checks.append({
                "name": "现金缓冲",
                "passed": bool(ok),
                "detail": f"当前总仓位 {current_position:.0%} + 建议 {signal.target_position:.0%} "
                          f"vs 现金缓冲下限 {settings.cash_buffer:.0%}",
            })

    # SELL / HOLD 只做信息记录
    if signal.action == "SELL":
        checks.append({"name": "卖出无风控限制", "passed": True, "detail": "卖出操作不做前置拦截"})

    passed = all(c["passed"] for c in checks)
    return RiskCheckResult(passed=passed, checks=checks)
