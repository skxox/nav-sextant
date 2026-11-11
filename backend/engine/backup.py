"""数据库每日备份：gzip 归档 + 按保留天数清理（ROADMAP F 组）。

金融数据（净值/信号/执行记录）丢失不可重建，单文件库无备份机制是运行
风险。流水线每日跑完后触发：backups/sextant-YYYYMMDD.db.gz，超出
保留天数的旧档自动清理。

实现要点：用 sqlite3 backup API 而非直接拷贝文件——WAL 模式下主库文件
不含未 checkpoint 的写入，裸拷会丢当天数据；backup API 走在线快照。
"""
from __future__ import annotations

import gzip
import logging
import re
import sqlite3
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^sextant-\d{4}-\d{2}-\d{2}\.db\.gz$")


def backup_database(db, keep_days: int = 30) -> Path | None:
    """压缩快照到 backups/ 目录，按保留天数清理旧档；keep_days=0 关闭。

    db: Database 实例（用其连接做在线快照）。返回本次备份路径。
    """
    if keep_days <= 0:
        return None
    backup_dir = db.path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"sextant-{date.today().isoformat()}.db.gz"

    snapshot = sqlite3.connect(":memory:")
    with db._lock:  # noqa: SLF001 — 同进程持锁做快照，避免与写入交错
        db.conn.backup(snapshot)
    try:
        with gzip.open(target, "wb", compresslevel=6) as f:
            for line in snapshot.iterdump():
                f.write(line.encode("utf-8"))
    finally:
        snapshot.close()
    _prune(backup_dir, keep_days)
    logger.info("数据库已备份：%s（保留 %d 天）", target, keep_days)
    return target


def _prune(backup_dir: Path, keep_days: int) -> list[Path]:
    """删除超出保留窗口的备份档（按文件名日期，不碰其他文件）。"""
    cutoff = date.fromordinal(date.today().toordinal() - keep_days)
    removed = []
    for p in sorted(backup_dir.glob("sextant-*.db.gz")):
        m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
        if m and date.fromisoformat(m.group(1)) < cutoff:
            p.unlink()
            removed.append(p)
    return removed


def latest_backup(backup_dir: Path | None = None) -> dict | None:
    """最新备份档信息（路径/大小/日期），供运行状态接口展示。"""
    backup_dir = backup_dir or (Path("data") / "backups")
    files = sorted(p for p in backup_dir.glob("sextant-*.db.gz") if _NAME_RE.match(p.name))
    if not files:
        return None
    p = files[-1]
    m = re.search(r"(\d{4}-\d{2}-\d{2})", p.name)
    return {"file": str(p), "size_bytes": p.stat().st_size, "date": m.group(1) if m else None}
