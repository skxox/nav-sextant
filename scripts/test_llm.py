"""LLM 连通性测试：调用配置的模型做一次 JSON 结构化输出。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import load_config
from backend.config_runtime import effective_config, ensure_settings_seeded
from backend.db import Database
from backend.llm.client import LLMClient

if __name__ == "__main__":
    db = Database(load_config().db_path)
    ensure_settings_seeded(db)
    cfg = effective_config(db)
    print(f"base_url={cfg.llm.base_url} model={cfg.llm.model}")
    client = LLMClient(cfg.llm)
    result = client.chat_json(
        "你是测试助手，严格输出 JSON。",
        '请输出 {"ok": true, "model_intro": "<一句话说明你是什么模型>"}',
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
