"""运行保障测试：每日备份 + 运行心跳 + 看门狗判定（ROADMAP F 组）。"""
from __future__ import annotations

import gzip
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from backend.db import Database
from backend.engine.backup import _prune, backup_database, latest_backup
from backend.engine.pipeline import Pipeline
from backend.engine.watchdog import runtime_issues


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "run.db")


# ---------- 备份 ----------

def test_backup_creates_restorable_archive(tmp_path):
    """备份文件 gunzip 后可直接重建库（含数据）——可恢复性是底线。"""
    db = _db(tmp_path)
    db.upsert_nav([("110022", "2026-11-06", 3.0, 3.0)])
    db.save_signal({
        "trade_date": "2026-11-06", "fund_code": "110022", "action": "BUY",
        "target_position": 0.3, "composite_score": 0.5,
        "score_technical": 0.1, "score_valuation": 0.1,
        "score_sentiment": 0.1, "score_risk": 0.1,
        "reasons_json": "[]", "risk_checks_json": "[]",
    })
    target = backup_database(db, keep_days=30)
    assert target is not None and target.exists()

    restored = tmp_path / "restored.db"
    conn = sqlite3.connect(restored)
    conn.executescript(gzip.open(target, "rt").read())
    nav = conn.execute("SELECT nav FROM fund_nav WHERE fund_code='110022'").fetchone()
    sig = conn.execute("SELECT action FROM signals WHERE fund_code='110022'").fetchone()
    conn.close()
    assert nav == (3.0,) and sig == ("BUY",)


def test_backup_off_and_prune(tmp_path):
    db = _db(tmp_path)
    assert backup_database(db, keep_days=0) is None  # 0 = 关闭

    backup_dir = db.path.parent / "backups"
    backup_dir.mkdir()
    # 造 35 天前与今天的档位，保留 30 天 → 旧档被清、新档保留
    old = backup_dir / "sextant-2026-10-01.db.gz"
    new = backup_dir / f"sextant-{datetime.now().date().isoformat()}.db.gz"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    (backup_dir / "unrelated.txt").write_bytes(b"x")  # 非备份文件不碰
    removed = _prune(backup_dir, keep_days=30)
    assert removed == [old] and not old.exists() and new.exists()
    assert (backup_dir / "unrelated.txt").exists()


def test_latest_backup_info(tmp_path):
    db = _db(tmp_path)
    assert latest_backup(db.path.parent / "backups") is None
    backup_database(db, keep_days=30)
    info = latest_backup(db.path.parent / "backups")
    assert info is not None and info["date"] == datetime.now().date().isoformat()
    assert info["size_bytes"] > 0


# ---------- 心跳 ----------

def test_log_run_and_last_run(tmp_path):
    db = _db(tmp_path)
    assert db.last_run() is None
    db.log_run("ok", trade_date="2026-11-10", ok_count=2, fail_count=0)
    last = db.last_run()
    assert last["status"] == "ok" and last["trade_date"] == "2026-11-10"
    assert last["ok_count"] == 2 and last["run_date"] == datetime.now().date().isoformat()
    db.log_run("skipped_non_trading_day")
    assert db.last_run()["status"] == "skipped_non_trading_day"


def test_pipeline_non_trading_day_logs_heartbeat(tmp_path, app_config, monkeypatch):
    """非交易日跳过也记心跳——证明调度活着，看门狗不误报。"""
    import backend.collectors.trading_calendar as cal
    monkeypatch.setattr(cal, "trade_dates", lambda: set())  # 今天非交易日
    db = Database(Path(app_config.db_path))
    assert Pipeline(app_config, db).run() == []
    assert db.last_run()["status"] == "skipped_non_trading_day"


# ---------- 看门狗判定 ----------

def test_issues_when_never_run_and_scheduler_on(tmp_path):
    db = _db(tmp_path)
    assert runtime_issues(db, scheduler_enabled=True) == ["调度器已启用但从未运行过流水线（pipeline_runs 空表）——服务可能没起来"]
    assert runtime_issues(db, scheduler_enabled=False) == []  # 手动模式不要求心跳


def test_issues_when_heartbeat_stale(tmp_path):
    db = _db(tmp_path)
    db.log_run("ok", trade_date="2026-11-10")
    # finished_at 由库写入 now，直接把判定时钟拨到 30 小时后
    assert any("未运行" in i for i in runtime_issues(db, True, now=datetime.now() + timedelta(hours=30)))
    assert runtime_issues(db, True, now=datetime.now() + timedelta(hours=2)) == []


def test_issues_when_last_run_failed(tmp_path):
    db = _db(tmp_path)
    db.log_run("failed", trade_date="2026-11-10", fail_count=2, detail="采集失败")
    issues = runtime_issues(db, True)
    assert any("全失败" in i and "采集失败" in i for i in issues)
