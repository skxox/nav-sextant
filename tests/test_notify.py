"""推送 actionable 化测试：操作参数渲染 + 现态上下文（ROADMAP E 组）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import AppConfig
from backend.contracts import AgentOutputModel, SignalModel
from backend.db import Database
from backend.engine.pipeline import Pipeline
from backend.notify import ActionContext, _subject, format_signal_text


def _agent(name: str, score: float, summary: str) -> AgentOutputModel:
    return AgentOutputModel(agent=name, score=score, direction="BUY",  # type: ignore[arg-type]
                            summary=summary)


def _signal(action: str = "BUY", target: float = 0.3, risk: bool = True,
            risk_summary: str = "", agents: dict | None = None) -> SignalModel:
    return SignalModel(
        trade_date="2026-11-10", fund_code="110022", fund_name="沪深300指数",
        action=action, target_position=target, composite_score=0.4,  # type: ignore[arg-type]
        risk_passed=risk, risk_summary=risk_summary, agents=agents or {})


# ---------- 操作参数渲染 ----------

def test_buy_with_full_context():
    s = _signal("BUY", 0.3, agents={
        "technical": _agent("technical", 0.8, "动量多头：5日/20日均线金叉；波动适中"),
        "valuation": _agent("valuation", -0.2, "估值中性"),
    })
    ctx = ActionContext(positions={"110022": 0.15}, portfolio_value=30000)
    text = format_signal_text([s], ctx)
    assert "买入 沪深300指数（110022）" in text
    assert "加仓至 30%（当前 15%，变动 +15% ≈ ¥4,500）" in text
    assert "理由：技术面：动量多头：5日/20日均线金叉" in text  # 方向最一致的分最高 Agent
    assert "15:00 前下单" in text
    assert "记录执行/跳过" in text


def test_sell_partial_and_full_exit():
    ctx = ActionContext(positions={"110022": 0.3}, portfolio_value=50000)
    s = _signal("SELL", 0.1, agents={"risk": _agent("risk", -0.6, "波动放大")})
    assert "减仓至 10%（当前 30%，变动 -20% ≈ ¥1.0万）" in format_signal_text([s], ctx)
    s2 = _signal("SELL", 0.0, agents={"risk": _agent("risk", -0.6, "波动放大")})
    assert "清仓 0%（当前 30%，变动 -30% ≈ ¥1.5万）" in format_signal_text([s2], ctx)


def test_blocked_signal_says_do_not_execute():
    s = _signal("BUY", 0.3, risk=False, risk_summary="单基仓位上限：目标 60% 超 40%")
    text = format_signal_text([s], None)
    assert "▶ 拦截 沪深300指数（110022）" in text
    assert "不执行：单基仓位上限" in text


def test_no_position_recorded():
    s = _signal("BUY", 0.3, agents={"technical": _agent("technical", 0.5, "动量修复")})
    assert "调仓至 30%（当前仓位未录入）" in format_signal_text([s], None)


def test_reason_picks_lowest_agent_for_sell():
    s = _signal("SELL", agents={
        "technical": _agent("technical", 0.7, "动量仍强"),
        "risk": _agent("risk", -0.5, "回撤触发风控"),
    })
    assert "理由：风险面：回撤触发风控" in format_signal_text([s], None)


def test_reason_truncated_to_one_line():
    s = _signal("BUY", agents={"sentiment": _agent(
        "sentiment", 0.6, "政策面利好频出" * 20 + "。后面的不出现")})
    line = [ln for ln in format_signal_text([s], None).splitlines() if ln.startswith("  理由")][0]
    assert len(line) <= 60 and "后面的不出现" not in line


def test_actionable_sorted_before_blocked():
    ok = _signal("BUY", 0.3)
    blocked = _signal("BUY", 0.3, risk=False, risk_summary="超限")
    text = format_signal_text([blocked, ok], None)
    assert text.index("买入 沪深300") < text.index("拦截 沪深300")


def test_empty_signals():
    assert format_signal_text([]) == "nav-sextant：今日无信号"


def test_subject_counts():
    ok = _signal("BUY")
    blocked = _signal("BUY", risk=False, risk_summary="x")
    assert _subject([ok, blocked]) == "nav-sextant 信号 2026-11-10（1 买 / 1 拦截）"
    assert _subject([]) == "nav-sextant 信号 （无操作）"


# ---------- 现态上下文（pipeline 查库）----------

def test_action_context_from_db(tmp_path, app_config: AppConfig):
    db = Database(Path(app_config.db_path))
    db.execute("INSERT OR REPLACE INTO fund_position (fund_code, position, shares, cost) "
               "VALUES ('110022', 0.15, 1000, 2900)")
    db.upsert_nav([("110022", "2026-11-05", 3.0, 3.0), ("110022", "2026-11-06", 3.1, 3.1)])
    db.execute("INSERT OR REPLACE INTO fund_position (fund_code, position, shares, cost) "
               "VALUES ('161725', 0.05, 500, 1.0)")
    # 161725 无净值：仓位仍收录，市值不计
    ctx = Pipeline(app_config, db)._action_context()
    assert ctx.positions["110022"] == pytest.approx(0.15)
    assert ctx.positions["161725"] == pytest.approx(0.05)
    assert ctx.portfolio_value == pytest.approx(1000 * 3.1)


def test_action_context_empty_db(tmp_path, app_config: AppConfig):
    ctx = Pipeline(app_config, Database(Path(app_config.db_path)))._action_context()
    assert ctx.positions == {} and ctx.portfolio_value is None
