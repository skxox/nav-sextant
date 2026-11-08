"""分层配置：数据库 app_settings 表为准，pydantic 内置默认兜底。

所有可编辑配置（LLM / 策略 / 风控 / 调度 / 告警 / 辩论 / 回测）存 DB，
由网页「设置」页维护，改完即生效（每次请求重新合成），无需重启；
config.yaml 只保留数据库路径等引导配置。合成时走同一套 pydantic 校验，
非法覆盖会被拒绝。旧版含完整配置的 config.yaml 会在启动时一次性迁入 DB。
"""
from __future__ import annotations

import copy
from functools import lru_cache

import yaml

from .config import AppConfig, PROJECT_ROOT
from .db import Database

# 可被 DB 覆盖的配置段（key 与 AppConfig 字段一一对应）
OVERRIDABLE_SECTIONS = ("llm", "settings", "scheduler", "notifications", "debate", "backtest")


@lru_cache(maxsize=1)
def _yaml_raw() -> dict:
    """YAML 引导配置只读一次（进程内缓存）。"""
    with open(PROJECT_ROOT / "config.yaml", "r", encoding="utf-8") as f:
        raw: dict = yaml.safe_load(f) or {}
    raw.setdefault("llm", {})
    raw.setdefault("database", {})
    if "db_path" not in raw:
        raw["db_path"] = PROJECT_ROOT / raw.get("database", {}).get("path", "data/sextant.db")
    return raw


def ensure_settings_seeded(db: Database) -> None:
    """一次性迁移：旧版 config.yaml 里的可覆盖配置段导入 app_settings。

    DB 中已有该段的覆盖时不动（页面配置优先）。YAML 瘦身后此函数为 no-op，
    保留它是为了兼容旧部署升级。
    """
    raw = _yaml_raw()
    existing = set(db.get_settings().keys())
    for section in OVERRIDABLE_SECTIONS:
        if section in existing:
            continue
        value = raw.get(section)
        if isinstance(value, dict) and value:
            db.set_setting(section, value)


def effective_config(db: Database) -> AppConfig:
    """合成生效配置：pydantic 内置默认 + app_settings 覆盖 + fund_config 基金表。"""
    raw = copy.deepcopy(_yaml_raw())
    overrides = db.get_settings()
    for section in OVERRIDABLE_SECTIONS:
        if section in overrides and isinstance(overrides[section], dict):
            raw.setdefault(section, {}).update(overrides[section])
    funds_override = db.get_fund_overrides()
    if funds_override:
        raw["funds"] = [{"code": f["code"], "name": f["name"],
                         "benchmark_index": f["benchmark_index"],
                         "valuation_index": f.get("valuation_index", ""),
                         "sector": f.get("sector", "")} for f in funds_override]
    return AppConfig.model_validate(raw)


def validate_section(db: Database, section: str, patch: dict) -> AppConfig:
    """校验单段配置补丁：合成"当前生效 + 补丁"后走完整 pydantic 校验，非法即抛 ValidationError。"""
    if section == "funds":
        raw = copy.deepcopy(_yaml_raw())
        raw["funds"] = patch.get("funds", [])
        return AppConfig.model_validate(raw)
    if section not in OVERRIDABLE_SECTIONS:
        raise ValueError(f"不支持的配置段: {section}")
    current = effective_config(db)
    merged = current.model_dump(exclude={"db_path", "funds"})
    merged[section] = {**merged.get(section, {}), **patch}
    # funds 需要原样带回（FundSpec 是 frozen 模型，model_dump 后可重新校验）
    merged["funds"] = [{"code": f.code, "name": f.name, "benchmark_index": f.benchmark_index,
                        "valuation_index": f.valuation_index, "sector": f.sector}
                       for f in current.funds]
    merged["db_path"] = current.db_path
    return AppConfig.model_validate(merged)
