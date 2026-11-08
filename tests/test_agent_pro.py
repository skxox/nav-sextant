"""Agent 升级后的专业机制单元测试：趋势对齐、ERP、分歧检测、self-consistency。

无网络依赖：LLM 用桩对象模拟采样输出。
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.agents.base import AgentOutput, AnalysisContext
from backend.agents.technical import TechnicalAgent
from backend.agents.valuation_agent import ValuationAgent
from backend.agents.risk_agent import RiskAgent
from backend.agents.sentiment import SentimentAgent, SentimentVerdict
from backend.engine.orchestrator import synthesize
from backend.config import Settings
from tests.conftest import make_nav_series, make_pe_series


# ---------- 技术面：趋势对齐与波动调整 ----------

def test_technical_uptrend_alignment():
    """单边上涨序列：趋势对齐应接近 1，分数为正。"""
    nav = pd.DataFrame({
        "trade_date": [f"d{i}" for i in range(120)],
        "nav": np.cumprod(1 + np.full(120, 0.004)),  # 单边上涨
    })
    out = TechnicalAgent().analyze(AnalysisContext(momentum=__import__(
        "backend.factors.momentum", fromlist=["compute_momentum"]).compute_momentum(nav)))
    assert out.score > 0
    assert out.detail["trend_alignment"] >= 0.9
    assert out.detail["above_ma20"] and out.detail["above_ma60"]
    assert out.data_quality == 1.0


def test_technical_whipsaw_reduces_confidence():
    """震荡市：趋势对齐低 → 置信度下降到 0.55。"""
    rng = np.random.default_rng(1)
    nav = pd.DataFrame({
        "trade_date": [f"d{i}" for i in range(200)],
        "nav": np.cumprod(1 + rng.normal(0, 0.015, 200)),
    })
    from backend.factors.momentum import compute_momentum
    mf = compute_momentum(nav)
    out = TechnicalAgent().analyze(AnalysisContext(momentum=mf))
    if mf.trend_alignment < 0.7:
        assert out.confidence == 0.55


import pandas as pd  # noqa: E402


# ---------- 估值面：ERP 与数据质量 ----------

def test_valuation_erp_fields():
    vf_df = make_pe_series()
    from backend.factors.valuation import compute_valuation
    vf = compute_valuation(vf_df, risk_free_rate=0.02)
    assert vf.earnings_yield_pct is not None
    assert vf.erp_pct is not None
    assert vf.erp_percentile is not None
    assert vf.data_quality == 1.0
    out = ValuationAgent().analyze(AnalysisContext(valuation=vf))
    assert "ERP" in out.summary


def test_valuation_data_quality_partial():
    """只有 PE 没有 PB/ERP 时数据质量下降。"""
    from backend.factors.valuation import compute_valuation
    pe_only = make_pe_series()[["trade_date", "pe"]].copy()
    pe_only["pb"] = np.nan
    vf = compute_valuation(pe_only)
    assert vf.pb_percentile is None
    assert vf.data_quality < 1.0


# ---------- 风险面：VaR 与波动率制度 ----------

def test_risk_var_and_regime():
    nav = make_nav_series(250, vol=0.02)
    out = RiskAgent().analyze(AnalysisContext(nav_history=nav))
    assert out.detail["var95_daily_pct"] is not None
    assert out.detail["var95_daily_pct"] > 0
    assert "high_vol_regime" in out.detail
    assert out.data_quality == 1.0


def test_risk_insufficient_history():
    nav = make_nav_series(20)
    out = RiskAgent().analyze(AnalysisContext(nav_history=nav))
    assert out.score == 0.0 and out.data_quality == 0.3


# ---------- 合成层：数据质量加权与分歧检测 ----------

def _out(agent: str, score: float, conf: float = 0.7, dq: float = 1.0) -> AgentOutput:
    return AgentOutput(agent=agent, score=score, confidence=conf, data_quality=dq)


def test_synthesize_data_quality_downweights():
    """同一观点，data_quality=0.2 的 Agent 影响应远小于 1.0 的。"""
    s = Settings()
    high = {"technical": _out("technical", 0.6, dq=1.0), "valuation": _out("valuation", 0.0, dq=1.0),
            "sentiment": _out("sentiment", 0.0, dq=1.0), "risk": _out("risk", 0.0, dq=1.0)}
    low = {"technical": _out("technical", 0.6, dq=0.2), "valuation": _out("valuation", 0.0, dq=1.0),
           "sentiment": _out("sentiment", 0.0, dq=1.0), "risk": _out("risk", 0.0, dq=1.0)}
    h = synthesize("c", "n", "d", high, s)
    l = synthesize("c", "n", "d", low, s)
    assert h.composite_score > l.composite_score


def test_synthesize_disagreement_tightens_threshold():
    """多空强分歧：原本能触发 BUY 的分数被收紧为 HOLD。"""
    s = Settings()
    agree = {"technical": _out("technical", 0.8), "valuation": _out("valuation", 0.6),
             "sentiment": _out("sentiment", 0.5), "risk": _out("risk", 0.4)}
    disagree = {**agree, "sentiment": _out("sentiment", -0.7)}
    a = synthesize("c", "n", "d", agree, s)
    b = synthesize("c", "n", "d", disagree, s)
    assert a.action in ("BUY", "SELL")
    assert b.disagreement is True
    assert abs(b.composite_score) < abs(a.composite_score)  # 分歧拉低合成分


# ---------- 消息面：self-consistency 与置信度校准 ----------

class StubLLM:
    """模拟 N 次采样输出，用于测试聚合逻辑。"""

    def __init__(self, outputs: list[dict]):
        self.outputs = list(outputs)
        self.calls = 0

    def chat_json(self, system_prompt: str, user_prompt: str, **_kwargs) -> dict:
        out = self.outputs[min(self.calls, len(self.outputs) - 1)]
        self.calls += 1
        return out


def _news(n: int) -> list[dict]:
    return [{"published": "2026-11-01 09:00", "title": f"新闻{i}", "content": "x"} for i in range(n)]


def test_sentiment_self_consistency_median():
    """三次采样：2 次利好 1 次中性 → 中位数取利好，样本记录完整。"""
    outs = [
        {"score": 0.6, "confidence": 0.7, "key_events": ["e1"], "reasoning": "r"},
        {"score": 0.5, "confidence": 0.7, "key_events": ["e1"], "reasoning": "r"},
        {"score": 0.1, "confidence": 0.7, "key_events": ["e1"], "reasoning": "r"},
    ]
    agent = SentimentAgent(llm=StubLLM(outs), samples=3)
    ctx = AnalysisContext(news=_news(5), fund_name="F", momentum=None, valuation=None, nav_history=None)
    out = agent.analyze(ctx)
    assert out.score == 0.5
    assert out.detail["samples"]["n"] == 3
    assert 0 < out.confidence <= 0.85


def test_sentiment_disagreement_lowers_confidence():
    """采样极差大 → 一致度低 → 置信度被压低。"""
    outs = [
        {"score": 1.0, "confidence": 0.9, "key_events": [], "reasoning": ""},
        {"score": -0.6, "confidence": 0.9, "key_events": [], "reasoning": ""},
        {"score": 0.2, "confidence": 0.9, "key_events": [], "reasoning": ""},
    ]
    agent = SentimentAgent(llm=StubLLM(outs), samples=3)
    ctx = AnalysisContext(news=_news(5), fund_name="F", momentum=None, valuation=None, nav_history=None)
    out = agent.analyze(ctx)
    assert out.detail["samples"]["spread"] > 0.8
    assert out.confidence < 0.5
    assert "分歧" in out.summary


def test_sentiment_few_news_caps_confidence():
    """新闻 < 3 条：置信度上限压到 0.35。"""
    outs = [{"score": 0.8, "confidence": 0.95, "key_events": [], "reasoning": ""}] * 3
    agent = SentimentAgent(llm=StubLLM(outs), samples=3)
    ctx = AnalysisContext(news=_news(2), fund_name="F", momentum=None, valuation=None, nav_history=None)
    out = agent.analyze(ctx)
    assert out.confidence <= 0.35
    assert out.data_quality == 0.6


def test_sentiment_invalid_output_retries_then_fallback():
    """非法输出重试仍失败 → fail-closed 中性回退。"""

    class BadLLM:
        def chat_json(self, *a, **k):
            return {"score": "not-a-number"}


    agent = SentimentAgent(llm=BadLLM(), samples=1)
    ctx = AnalysisContext(news=_news(5), fund_name="F", momentum=None, valuation=None, nav_history=None)
    out = agent.analyze(ctx)
    assert out.fallback_used and out.score == 0.0


def test_sentiment_verdict_rejects_out_of_range():
    with pytest.raises(Exception):
        SentimentVerdict.model_validate({"score": 5.0})
