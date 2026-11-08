"""流水线 smoke test：跑一次完整分析并打印结果。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import load_config
from backend.config_runtime import effective_config, ensure_settings_seeded
from backend.db import Database
from backend.engine.pipeline import Pipeline

if __name__ == "__main__":
    force = "--force" in sys.argv
    db = Database(load_config().db_path)
    ensure_settings_seeded(db)
    results = Pipeline(effective_config(db), db).run(force=force)
    for r in results:
        sent = r.agents.get("sentiment")
        print(f"{r.fund_code} {r.fund_name}: {r.action} score={r.composite_score} "
              f"risk={r.risk_summary} debate={'维持' if r.debate.agree else '否决' if r.debate.triggered else '未触发'}")
        for name, a in r.agents.items():
            print(f"    [{name}] {a.direction} {a.score} (conf={a.confidence}, fallback={a.fallback_used}) {a.summary[:80]}")
