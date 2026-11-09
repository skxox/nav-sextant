"""信号视图重建测试：/signals/latest 与流水线幂等路径共用。"""
from __future__ import annotations

import json

from backend.db import Database
from backend.engine.pipeline import rebuild_signal_views


def _seed(db: Database, debate_detail: dict, debate_summary: str) -> None:
    db.save_signal({
        "trade_date": "2026-11-06", "fund_code": "161725", "action": "HOLD",
        "target_position": 0.0, "composite_score": 0.52,
        "score_technical": 0.8, "score_valuation": 0.5, "score_sentiment": 0.2, "score_risk": 0.3,
        "reasons_json": json.dumps(["[valuation] 测试"], ensure_ascii=False),
        "risk_checks_json": json.dumps([{"name": "现金缓冲", "passed": True, "detail": "ok"}]),
    })
    db.save_agent_log({
        "trade_date": "2026-11-06", "fund_code": "161725", "agent": "technical",
        "score": 0.8, "direction": "BUY", "confidence": 0.7, "summary": "动量多头",
        "fallback_used": 0, "detail_json": json.dumps({"momentum": 3.0}), "data_quality": 1.0,
    })
    db.save_agent_log({
        "trade_date": "2026-11-06", "fund_code": "161725", "agent": "debate",
        "score": 0.0, "direction": "HOLD", "confidence": 0.65,
        "summary": debate_summary, "fallback_used": 0,
        "detail_json": json.dumps(debate_detail, ensure_ascii=False), "data_quality": 1.0,
    })


def _rows(db: Database):
    return db.query("SELECT * FROM signals")


def test_rebuild_agents_and_debate_new_format(tmp_path):
    db = Database(tmp_path / "v.db")
    _seed(db, {
        "triggered": True, "agree": False, "final_action": "HOLD", "confidence": 0.65,
        "bull": ["低估值"], "bear": ["外部冲击"], "rounds": [{"round": 1}],
        "rounds_used": 2, "judge_reasoning": "Bear 更有说服力", "unavailable_reason": "",
    }, "辩论否决信号 → HOLD（2 轮）；Bear 更有说服力")
    (sig,) = rebuild_signal_views(db, _rows(db), {"161725": "招商中证白酒"})
    assert sig.fund_name == "招商中证白酒"
    assert "debate" not in sig.agents and set(sig.agents) == {"technical"}
    assert sig.agents["technical"].score == 0.8 and sig.agents["technical"].detail == {"momentum": 3.0}
    assert sig.debate.triggered and not sig.debate.agree
    assert sig.debate.judge_reasoning == "Bear 更有说服力"
    assert sig.debate.rounds_used == 2 and sig.debate.bear_points == ["外部冲击"]
    assert sig.risk_passed, "检查全过 → passed"
    assert sig.reasons == ["[valuation] 测试"]


def test_rebuild_debate_legacy_format(tmp_path):
    """旧版 detail_json 无 agree/judge 字段 → 从 summary 模板反解。"""
    db = Database(tmp_path / "v.db")
    _seed(db, {"triggered": True, "bull": ["b"], "bear": ["e"],
               "rounds": [], "rounds_used": 2}, "辩论否决信号 → HOLD（2 轮）；旧格式裁决理由")
    (sig,) = rebuild_signal_views(db, _rows(db))
    assert not sig.debate.agree, "summary 含「否决」→ agree=False"
    assert sig.debate.judge_reasoning == "旧格式裁决理由"
    assert sig.agents["technical"].data_quality == 1.0


def test_rebuild_risk_failed_checks(tmp_path):
    db = Database(tmp_path / "v.db")
    db.save_signal({
        "trade_date": "2026-11-06", "fund_code": "161725", "action": "HOLD",
        "target_position": 0.0, "composite_score": 0.5,
        "score_technical": 0.5, "score_valuation": 0.5, "score_sentiment": 0.5, "score_risk": 0.5,
        "reasons_json": "[]",
        "risk_checks_json": json.dumps([{"name": "现金缓冲", "passed": False, "detail": "缺仓位"}]),
    })
    (sig,) = rebuild_signal_views(db, _rows(db))
    assert not sig.risk_passed and sig.risk_checks[0].name == "现金缓冲"


def test_rebuild_missing_logs_gives_empty_shell(tmp_path):
    """无 agent_logs（如手工导入的信号）→ 空 agents + 默认 debate，不报错。"""
    db = Database(tmp_path / "v.db")
    db.save_signal({
        "trade_date": "2026-11-06", "fund_code": "161725", "action": "HOLD",
        "target_position": 0.0, "composite_score": 0.5,
        "score_technical": 0.5, "score_valuation": 0.5, "score_sentiment": 0.5, "score_risk": 0.5,
        "reasons_json": "[]", "risk_checks_json": "[]",
    })
    (sig,) = rebuild_signal_views(db, _rows(db))
    assert sig.agents == {} and not sig.debate.triggered
