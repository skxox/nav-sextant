#!/usr/bin/env python3
"""运行看门狗（crontab 用）：服务挂掉 / 调度未跑 / 最近运行全失败时推送告警。

用法（工作目录 = 项目根，先 `uv sync`）：
    uv run python scripts/watchdog.py [--url http://127.0.0.1:8000] [--stale-hours 26] [--dry-run]

crontab 示例（每天 08:30 检查一次；服务应有自己的常驻方式，如 nohup/launchd）：
    30 8 * * * cd /path/to/nav-sextant && uv run python scripts/watchdog.py >> .tmp/watchdog.log 2>&1

告警通道用设置页配置的飞书/企微 webhook（数据库 notifications 段）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="nav-sextant 运行看门狗")
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="服务地址")
    ap.add_argument("--stale-hours", type=int, default=26, help="心跳过期阈值（小时）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不推送")
    args = ap.parse_args()

    issues: list[str] = []

    # 1) 服务存活探测
    try:
        requests.get(f"{args.url}/api/meta", timeout=5).raise_for_status()
        print(f"[ok] 服务在线：{args.url}")
    except Exception as e:  # noqa: BLE001
        issues.append(f"服务不可达（{args.url}）：{e}")

    # 2) 数据层心跳检查（服务挂了也能查：直接读库）
    from backend.config import load_config
    from backend.config_runtime import effective_config
    from backend.db import Database
    from backend.engine.watchdog import push_alert, runtime_issues

    cfg = load_config()
    db = Database(cfg.db_path)
    eff = effective_config(db)
    for issue in runtime_issues(db, eff.scheduler.enabled, stale_hours=args.stale_hours):
        issues.append(issue)

    if not issues:
        last = db.last_run()
        print(f"[ok] 心跳正常：最近运行 {last['finished_at'] if last else '（从未）'}")
        return 0

    text = "nav-sextant 看门狗告警：\n" + "\n".join(f"· {i}" for i in issues)
    print(text)
    if args.dry_run:
        return 1
    hook = eff.notifications.feishu_webhook or eff.notifications.wecom_webhook
    if not hook:
        print("[warn] 未配置飞书/企微 webhook，告警仅打印（设置页 → 告警通知）")
        return 1
    print("[ok] 已推送" if push_alert(hook, text) else "[warn] 推送失败，见上")
    return 1


if __name__ == "__main__":
    sys.exit(main())
