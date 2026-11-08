"""编排合成与硬风控测试。"""
from __future__ import annotations

import pytest

from backend.agents.base import AgentOutput
from backend.config import Settings
from backend.engine.orchestrator import synthesize
from backend.engine.risk_control import check


def make_outputs(**overrides) -> dict[str, AgentOutput]:
    base = {
        "technical": AgentOutput(agent="technical", score=0.5, confidence=0.7),
        "valuation": AgentOutput(agent="valuation", score=0.5, confidence=0.7),
        "sentiment": AgentOutput(agent="sentiment", score=0.5, confidence=0.5),
        "risk": AgentOutput(agent="risk", score=0.5, confidence=0.7),
    }
    base.update(overrides)
    return base


def test_synthesize_all_positive_gives_buy():
    settings = Settings()
    sig = synthesize("110022", "测试", "2026-01-01", make_outputs(), settings)
    assert sig.action == "BUY"
    assert sig.composite_score > settings.thresholds.buy
    assert 0 < sig.target_position <= settings.max_single_fund_position


def test_synthesize_low_confidence_downweighted():
    settings = Settings()
    strong_conf = make_outputs()
    # 消息面给出强空观点但置信度极低：应被降权，几乎不影响合成结果
    weak_conf = make_outputs(sentiment=AgentOutput(agent="sentiment", score=-1.0, confidence=0.1))
    a = synthesize("110022", "测试", "2026-01-01", strong_conf, settings)
    b = synthesize("110022", "测试", "2026-01-01", weak_conf, settings)
    assert b.composite_score < a.composite_score, "低置信度的极端观点应被降权"
    assert a.composite_score - b.composite_score < 0.15, "降权后极端观点影响应有限"


def test_synthesize_hold_zone():
    settings = Settings()
    flat = make_outputs(**{k: AgentOutput(agent=k, score=0.05, confidence=0.5) for k in
                           ("technical", "valuation", "sentiment", "risk")})
    sig = synthesize("110022", "测试", "2026-01-01", flat, settings)
    assert sig.action == "HOLD"
    assert sig.target_position == 0.0


def test_risk_blocks_buy_over_cash_buffer():
    settings = Settings()
    sig = synthesize("110022", "测试", "2026-01-01", make_outputs(), settings)
    sig.target_position = 0.5
    result = check(sig, settings, current_position=0.6, portfolio_drawdown=-0.05)
    assert not result.passed
    assert any(c["name"] == "现金缓冲" and not c["passed"] for c in result.checks)


def test_risk_blocks_buy_on_deep_drawdown():
    settings = Settings()
    sig = synthesize("110022", "测试", "2026-01-01", make_outputs(), settings)
    result = check(sig, settings, current_position=0.0, portfolio_drawdown=-0.20)
    assert not result.passed
    assert any(c["name"] == "组合最大回撤" and not c["passed"] for c in result.checks)


def test_risk_fail_closed_when_position_unknown():
    settings = Settings()
    sig = synthesize("110022", "测试", "2026-01-01", make_outputs(), settings)
    result = check(sig, settings, current_position=None, portfolio_drawdown=None)
    assert not result.passed, "仓位数据缺失时必须 fail-closed"


def test_risk_sell_always_passes():
    settings = Settings()
    sig = synthesize("110022", "测试", "2026-01-01", make_outputs(), settings)
    sig.action = "SELL"
    sig.target_position = 0.0
    result = check(sig, settings, current_position=None, portfolio_drawdown=None)
    assert result.passed
