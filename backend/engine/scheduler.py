"""内置每日调度（APScheduler）：可选，替代外部 cron。

接受 AppConfig 或配置提供函数（后者在触发时读取最新生效配置，页面改配置即生效）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Union

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from ..config import AppConfig
from ..db import Database
from .pipeline import Pipeline

logger = logging.getLogger(__name__)

ConfigProvider = Union[AppConfig, Callable[[], AppConfig]]


class DailyScheduler:
    def __init__(self, config_provider: ConfigProvider, db: Database):
        self._provider = config_provider
        self.db = db
        self._scheduler: BackgroundScheduler | None = None

    def _cfg(self) -> AppConfig:
        return self._provider() if callable(self._provider) else self._provider

    def start(self) -> None:
        sch = self._cfg().scheduler
        if not sch.enabled or self._scheduler is not None:
            return
        hour, minute = sch.run_at.split(":")
        self._scheduler = BackgroundScheduler(timezone="Asia/Shanghai")
        self._scheduler.add_job(
            self._run_job,
            CronTrigger(day_of_week="mon-fri" if sch.weekdays_only else "*", hour=int(hour), minute=int(minute)),
            id="daily-pipeline",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self._scheduler.start()
        logger.info("内置调度器已启动：每%s %s 运行分析流水线",
                    "个交易日" if sch.weekdays_only else "天", sch.run_at)

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
            logger.info("内置调度器已停止")

    def _run_job(self) -> None:
        logger.info("定时任务触发：%s", datetime.now().strftime("%Y-%m-%d %H:%M"))
        try:
            Pipeline(self._cfg(), self.db).run()
        except Exception:  # noqa: BLE001
            logger.exception("定时流水线执行失败")
