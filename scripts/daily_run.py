"""每日定时运行入口（配合 cron / launchd，用 uv 运行）。

示例（每个交易日 18:00 运行）：
  crontab: 0 18 * * 1-5  cd /path/to/nav-sextant && /Users/cloudcsp/.local/bin/uv run python scripts/daily_run.py

配置（含 LLM）从数据库合成，与网页设置页保持一致。
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import load_config
from backend.config_runtime import effective_config, ensure_settings_seeded
from backend.db import Database
from backend.engine.pipeline import Pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

if __name__ == "__main__":
    db = Database(load_config().db_path)
    ensure_settings_seeded(db)
    results = Pipeline(effective_config(db), db).run()
    for r in results:
        if "error" in r:
            logging.error("%s %s 失败: %s", r["fund_code"], r["fund_name"], r["error"])
        else:
            logging.info("%s %s → %s (score=%.3f, 风控%s)",
                         r["fund_code"], r["fund_name"], r["action"],
                         r["composite_score"], "通过" if r["risk_passed"] else "拦截")
