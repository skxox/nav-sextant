"""运行看门狗：调度未跑/服务挂掉时的外部告警（ROADMAP F 组，cron 级方案）。

判定依据（数据层，不依赖服务存活）：
- 从未跑过：pipeline_runs 空表且调度器开着 → 配了自动跑却从未出过心跳
- 心跳过期：最近一次运行（含跳过/失败）距 now 超过 stale_hours →
  要么服务挂了，要么调度器停了（非交易日跳过同样写心跳，不会误报）

scripts/watchdog.py 是薄 CLI 封装：先探测服务 HTTP，再做数据层检查，
发现问题走配置的飞书/企微 webhook 推送，退出码 1 供 cron 生态使用。
"""
from __future__ import annotations

from datetime import datetime

from ..db import Database

STALE_DEFAULT_HOURS = 26  # 每日 18:00 跑，跨一个自然日仍未心跳即异常


def runtime_issues(db: Database, scheduler_enabled: bool, now: datetime | None = None,
                   stale_hours: int = STALE_DEFAULT_HOURS) -> list[str]:
    """返回问题描述列表（空 = 一切正常）。"""
    issues: list[str] = []
    now = now or datetime.now()
    last = db.last_run()
    if last is None:
        if scheduler_enabled:
            issues.append("调度器已启用但从未运行过流水线（pipeline_runs 空表）——服务可能没起来")
    else:
        finished = datetime.fromisoformat(last["finished_at"])
        age_h = (now - finished).total_seconds() / 3600
        if age_h > stale_hours:
            issues.append(
                f"流水线已 {age_h:.0f} 小时未运行（最近一次 {last['finished_at']} "
                f"状态 {last['status']}）——服务挂掉或调度停了")
        elif last["status"] == "failed":
            issues.append(f"最近一次运行全失败（{last['finished_at']}）：{last['detail'] or '见服务日志'}")
    return issues


def push_alert(webhook: str, text: str) -> bool:
    """飞书/企微 webhook 通用文本推送（两家 text 格式不同，各自尝试）。"""
    import requests
    ok = False
    for url, payload in (
        (webhook, {"msg_type": "text", "content": {"text": text}}),          # 飞书
        (webhook, {"msgtype": "text", "text": {"content": text}}),           # 企微
    ):
        try:
            if requests.post(url, json=payload, timeout=10).status_code < 300:
                ok = True
                break
        except Exception:  # noqa: BLE001
            continue
    return ok
