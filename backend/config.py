"""nav-sextant 配置模型（pydantic 强校验）。

配置分层（见 config_runtime.py）：
- config.yaml 只保留引导配置（数据库路径等无法放 DB 的项）；
- 其余全部配置（LLM / 策略 / 风控 / 辩论 / 调度 / 告警 / 自选基金）存数据库，
  由网页「设置」页维护，改完即时生效；
- 本模块的 pydantic 默认值即"内置出厂默认"，DB 覆盖项在其上叠加，
  合成时统一校验：权重归一、阈值关系、仓位边界等错误即时暴露。
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FundSpec(BaseModel):
    """单只基金的静态配置。"""
    code: str = Field(..., pattern=r"^\d{6}$", description="基金代码，6 位数字")
    name: str = Field(..., min_length=1)
    benchmark_index: str = Field(default="000300", pattern=r"^\d{6}$", description="持仓映射的基准指数")
    valuation_index: str = Field(default="", pattern=r"^(|\d{6})$",
                                 description="估值专用指数（PE/PB 分位来源）；留空使用 benchmark_index")
    sector: str = Field(default="", description="关联板块（如 消费/白酒/科技），页面可维护，留空则按名称推断")

    model_config = {"frozen": True}

    def effective_valuation_index(self) -> str:
        return self.valuation_index or self.benchmark_index


class Weights(BaseModel):
    """多维评分权重，必须归一。"""
    technical: float = Field(default=0.30, ge=0, le=1)
    valuation: float = Field(default=0.30, ge=0, le=1)
    sentiment: float = Field(default=0.25, ge=0, le=1)
    risk: float = Field(default=0.15, ge=0, le=1)

    @model_validator(mode="after")
    def _sum_to_one(self) -> "Weights":
        total = self.technical + self.valuation + self.sentiment + self.risk
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"评分权重之和必须为 1，当前 {total:.4f}")
        return self


class Thresholds(BaseModel):
    """合成信号阈值（-1 ~ 1）。"""
    buy: float = Field(default=0.25, ge=-1, le=1)
    sell: float = Field(default=-0.25, ge=-1, le=1)

    @model_validator(mode="after")
    def _buy_gt_sell(self) -> "Thresholds":
        if self.buy <= self.sell:
            raise ValueError(f"买入阈值({self.buy})必须大于卖出阈值({self.sell})")
        return self


class RiskSettings(BaseModel):
    max_single_fund_position: float = Field(default=0.30, gt=0, le=1)
    max_drawdown: float = Field(default=0.15, gt=0, lt=1)
    cash_buffer: float = Field(default=0.10, ge=0, lt=1)


class Settings(BaseModel):
    weights: Weights = Field(default_factory=Weights)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    momentum_windows: list[int] = Field(default_factory=lambda: [5, 20, 60], min_length=1)
    momentum_windows_weights: list[float] = Field(default_factory=lambda: [0.2, 0.3, 0.5])
    valuation_percentile_window: int = Field(default=750, ge=60)
    risk: RiskSettings = Field(default_factory=RiskSettings)

    # 兼容旧字段的便捷属性（engine 层使用）
    @property
    def max_single_fund_position(self) -> float:
        return self.risk.max_single_fund_position

    @property
    def max_drawdown(self) -> float:
        return self.risk.max_drawdown

    @property
    def cash_buffer(self) -> float:
        return self.risk.cash_buffer

    @model_validator(mode="after")
    def _windows_match(self) -> "Settings":
        if len(self.momentum_windows_weights) != len(self.momentum_windows):
            raise ValueError("momentum_windows 与 momentum_windows_weights 长度不一致")
        if any(w <= 0 for w in self.momentum_windows):
            raise ValueError("动量窗口必须为正整数")
        return self


class LLMSettings(BaseModel):
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "LLM_API_KEY"
    api_key: str = ""
    # model 允许为空：首次部署未在设置页配置 LLM 时系统仍可启动（仅量化层工作）
    model: str = Field(default="", description="模型 ID；为空表示 LLM 未配置，消息面/辩论层不可用")
    model_name: str = Field(default="", description="人类可读模型名，如 DeepSeek-v4-Flash-0731")
    vision_model: str = Field(default="", description="多模态视觉模型 ID，用于持仓截图 OCR 导入；留空禁用图片导入")
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    fallback_enabled: bool = True
    # Bull/Bear 辩论层：非 HOLD 信号触发，增加 3 次 LLM 调用
    debate_enabled: bool = True
    # 消息面 self-consistency 采样数（>1 时多采样取中位数，一致度校准置信度）
    sentiment_samples: int = Field(default=3, ge=1, le=5)
    # LLM 进程内语义缓存：同 prompt TTL 内零成本复用（采样调用不受影响）
    cache_ttl_minutes: int = Field(default=360, ge=0, le=10080)

    @model_validator(mode="after")
    def _normalize_base_url(self) -> "LLMSettings":
        # OpenAI SDK 只需要到 /v1；容错误填完整 endpoint
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[: -len("/chat/completions")]
        return self

    @property
    def api_key_resolved(self) -> str:
        return os.environ.get(self.api_key_env, "") or self.api_key


class SchedulerSettings(BaseModel):
    """内置每日定时任务（APScheduler）。enabled=false 时仅手动触发。"""
    enabled: bool = False
    run_at: str = Field(default="18:00", pattern=r"^\d{2}:\d{2}$")
    weekdays_only: bool = True


class NotificationSettings(BaseModel):
    """告警触达：BUY/SELL 信号或风控拦截时推送。全部留空则禁用。"""
    enabled: bool = False
    # 飞书自定义机器人 Webhook，如 https://open.feishu.cn/open-apis/bot/v2/hook/xxx
    feishu_webhook: str = ""
    # 企业微信群机器人 Webhook，如 https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx
    wecom_webhook: str = ""
    # SMTP 邮件
    email_to: list[str] = Field(default_factory=list)
    smtp_host: str = ""
    smtp_port: int = Field(default=465, ge=1, le=65535)
    smtp_user: str = ""
    smtp_password_env: str = "SMTP_PASSWORD"
    email_from: str = ""


class BacktestSettings(BaseModel):
    buy_cost: float = Field(default=0.0015, ge=0, lt=0.05, description="申购费率")
    sell_cost: float = Field(default=0.005, ge=0, lt=0.05, description="赎回费率")
    risk_free_rate: float = Field(default=0.02, ge=0, le=0.2, description="夏普比率无风险利率")


class DebateSettings(BaseModel):
    """Bull/Bear 多轮辩论。rounds=1 时为单轮立论+反驳；每加一轮多 2 次 LLM 调用。"""
    rounds: int = Field(default=1, ge=1, le=4, description="辩论轮数（1~4）")
    # 提前终局：一方置信度 >=0.85 且对方 <=0.25 时视为压倒性优势，停止后续轮次
    consensus_early_stop: bool = True


class AppConfig(BaseModel):
    # funds 允许为空：首次部署在设置页添加基金即可，YAML 不再承载基金列表
    funds: list[FundSpec] = Field(default_factory=list)
    settings: Settings = Field(default_factory=Settings)
    llm: LLMSettings
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    backtest: BacktestSettings = Field(default_factory=BacktestSettings)
    debate: DebateSettings = Field(default_factory=DebateSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    db_path: Path

    @model_validator(mode="after")
    def _unique_funds(self) -> "AppConfig":
        codes = [f.code for f in self.funds]
        if len(codes) != len(set(codes)):
            raise ValueError(f"基金代码重复: {codes}")
        return self


def load_config(path: Path | None = None) -> AppConfig:
    """加载并校验配置。配置不合法时抛 pydantic.ValidationError。"""
    cfg_path = path or (PROJECT_ROOT / "config.yaml")
    with open(cfg_path, "r", encoding="utf-8") as f:
        raw: dict = yaml.safe_load(f) or {}
    raw.setdefault("llm", {})
    raw.setdefault("database", {})
    if "db_path" not in raw:
        raw["db_path"] = PROJECT_ROOT / raw.get("database", {}).get("path", "data/sextant.db")
    return AppConfig.model_validate(raw)
