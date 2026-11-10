"""执行记录测试：记录/改写/回查净值/状态解析/统计（决策日志，ROADMAP A 组）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.db import Database
from backend.engine.executions import (
    ExecutionError, execution_stats, list_executions, record_execution,
)


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "exec.db")


def _seed_signal(db: Database, code: str, day: str, action: str,
                 target: float = 0.3) -> None:
    db.save_signal({
        "trade_date": day, "fund_code": code, "action": action,
        "target_position": target, "composite_score": 0.4,
        "score_technical": 0.1, "score_valuation": 0.1,
        "score_sentiment": 0.1, "score_risk": 0.1,
        "reasons_json": "[]", "risk_checks_json": "[]",
    })


# ---------- 记录与状态解析 ----------

def test_record_resolves_nav_from_fund_nav(tmp_path):
    """净值未填时按执行日回查官方净值 → 已确认，份额 = 金额/净值。"""
    db = _db(tmp_path)
    _seed_signal(db, "110022", "2026-06-01", "BUY")
    db.upsert_nav([("110022", "2026-06-02", 1.25, 1.25)])
    row = record_execution(db, "110022", "2026-06-02", "BUY",
                           signal_date="2026-06-01", amount=10000)
    assert row["status"] == "confirmed"
    assert row["nav"] == pytest.approx(1.25) and row["nav_source"] == "nav"
    assert row["shares"] == pytest.approx(8000.0)  # 10000 / 1.25
    assert row["signal_action"] == "BUY" and row["signal_target"] == pytest.approx(0.3)


def test_pending_when_nav_not_published(tmp_path):
    """执行日净值尚未发布（T+1 常态）→ 待确认，份额为空。"""
    db = _db(tmp_path)
    _seed_signal(db, "110022", "2026-06-01", "SELL")
    row = record_execution(db, "110022", "2026-06-02", "SELL",
                           signal_date="2026-06-01", amount=5000)
    assert row["status"] == "pending" and row["nav"] is None
    assert row["shares"] is None and row["nav_source"] is None
    # 净值次日入库后转确认（列表实时回查，无需改记录）
    db.upsert_nav([("110022", "2026-06-02", 2.0, 2.0)])
    assert list_executions(db)[0]["status"] == "confirmed"


def test_manual_nav_overrides_lookup(tmp_path):
    db = _db(tmp_path)
    _seed_signal(db, "110022", "2026-06-01", "BUY")
    db.upsert_nav([("110022", "2026-06-02", 1.25, 1.25)])  # 官方价不同也不覆盖
    row = record_execution(db, "110022", "2026-06-02", "BUY",
                           signal_date="2026-06-01", amount=1000, nav=1.30)
    assert row["nav"] == pytest.approx(1.30) and row["nav_source"] == "manual"
    assert row["shares"] == pytest.approx(769.23, abs=0.01)


def test_skip_record(tmp_path):
    db = _db(tmp_path)
    _seed_signal(db, "110022", "2026-06-01", "BUY")
    row = record_execution(db, "110022", "2026-06-01", "SKIP",
                           signal_date="2026-06-01", amount=1000, note="已满仓")
    assert row["status"] == "skipped" and row["action"] == "SKIP"
    assert row["amount"] is None and row["nav"] is None  # 未执行：金额被丢弃
    assert row["note"] == "已满仓"


def test_manual_trade_without_signal(tmp_path):
    """无 signal_date 的人工操作：不校验信号存在，可多条并存。"""
    db = _db(tmp_path)
    record_execution(db, "110022", "2026-06-02", "BUY", amount=2000)
    record_execution(db, "110022", "2026-06-03", "BUY", amount=3000)
    rows = list_executions(db)
    assert len(rows) == 2 and all(r["signal_date"] is None for r in rows)


def test_resignal_overwrites_same_signal(tmp_path):
    """同一信号再次记录（如改金额）→ 改写而非新增。"""
    db = _db(tmp_path)
    _seed_signal(db, "110022", "2026-06-01", "BUY")
    record_execution(db, "110022", "2026-06-02", "BUY",
                     signal_date="2026-06-01", amount=1000)
    record_execution(db, "110022", "2026-06-02", "BUY",
                     signal_date="2026-06-01", amount=2500)
    rows = list_executions(db)
    assert len(rows) == 1 and rows[0]["amount"] == 2500


# ---------- 校验 ----------

def test_invalid_action_rejected(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ExecutionError, match="action"):
        record_execution(db, "110022", "2026-06-02", "HOLD")


def test_bad_date_rejected(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ExecutionError, match="exec_date"):
        record_execution(db, "110022", "2026/06/02", "BUY")
    with pytest.raises(ExecutionError, match="signal_date"):
        record_execution(db, "110022", "2026-06-02", "BUY", signal_date="06-01")


def test_skip_requires_signal(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ExecutionError, match="关联"):
        record_execution(db, "110022", "2026-06-02", "SKIP")


def test_unknown_signal_rejected(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(ExecutionError, match="无信号"):
        record_execution(db, "110022", "2026-06-02", "BUY", signal_date="2026-06-01")


def test_nonpositive_amount_rejected(tmp_path):
    db = _db(tmp_path)
    _seed_signal(db, "110022", "2026-06-01", "BUY")
    with pytest.raises(ExecutionError, match="金额"):
        record_execution(db, "110022", "2026-06-02", "BUY",
                         signal_date="2026-06-01", amount=0)
    with pytest.raises(ExecutionError, match="净值"):
        record_execution(db, "110022", "2026-06-02", "BUY",
                         signal_date="2026-06-01", amount=100, nav=0)


# ---------- 列表与统计 ----------

def test_list_filter_fund_and_limit(tmp_path):
    db = _db(tmp_path)
    for code in ("110022", "161725"):
        for i in range(1, 6):
            record_execution(db, code, f"2026-06-0{i}", "BUY", amount=1000)
    assert len(list_executions(db, fund_code="110022")) == 5
    assert len(list_executions(db, limit=3)) == 3
    assert list_executions(db, fund_names={"110022": "测试基金", "161725": "白酒"})[0]["fund_name"] in ("测试基金", "白酒")
    assert list_executions(db, fund_code="110022")[0]["exec_date"] == "2026-06-05"  # 倒序


def test_stats_counts_by_disposition(tmp_path):
    """可执行信号 4 条：执行 2、跳过 1、未处理 1；HOLD 不计入。"""
    db = _db(tmp_path)
    days = [f"2026-06-0{i}" for i in range(1, 5)]
    _seed_signal(db, "110022", days[0], "BUY")    # 执行
    _seed_signal(db, "110022", days[1], "SELL")   # 执行
    _seed_signal(db, "110022", days[2], "BUY")    # 跳过
    _seed_signal(db, "110022", days[3], "SELL")   # 未处理
    _seed_signal(db, "110022", "2026-06-05", "HOLD")  # 不计入
    record_execution(db, "110022", "2026-06-02", "BUY", signal_date=days[0], amount=1000)
    record_execution(db, "110022", "2026-06-03", "SELL", signal_date=days[1], amount=1000)
    record_execution(db, "110022", "2026-06-03", "SKIP", signal_date=days[2], note="观望")
    stats = execution_stats(db)
    assert stats == {"actionable": 4, "executed": 2, "skipped": 1,
                     "unhandled": 1, "exec_rate": pytest.approx(0.5)}


def test_stats_empty(tmp_path):
    assert execution_stats(_db(tmp_path)) == {
        "actionable": 0, "executed": 0, "skipped": 0, "unhandled": 0, "exec_rate": None}
