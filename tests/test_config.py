"""净值估算与配置校验测试。"""
from __future__ import annotations

import pandas as pd
import pytest
from pydantic import ValidationError

from backend.config import AppConfig, FundSpec, LLMSettings, Settings, Weights, Thresholds
from backend.factors.nav_estimate import estimate_nav


def test_estimate_nav_basic(nav_df):
    holdings = pd.DataFrame({"weight": [10.0, 8.0]})
    index_close = pd.DataFrame({
        "trade_date": ["2026-01-01", "2026-01-02"],
        "close": [4000.0, 4040.0],
    })
    est = estimate_nav(nav_df, holdings, index_close)
    assert est is not None
    assert est.estimated_change_pct == pytest.approx(1.0 * 18.0 / 100, abs=1e-6)
    assert est.estimated_nav is not None and est.estimated_nav > est.latest_official_nav


def test_estimate_nav_missing_data(nav_df):
    est = estimate_nav(nav_df, pd.DataFrame(), pd.DataFrame())
    assert est is not None and est.estimated_nav is None


def test_estimate_nav_empty_nav():
    assert estimate_nav(pd.DataFrame(), pd.DataFrame(), pd.DataFrame()) is None


# ---------- 配置校验 ----------

def test_weights_must_sum_to_one():
    with pytest.raises(ValidationError):
        Weights(technical=0.5, valuation=0.5, sentiment=0.5, risk=0.5)


def test_thresholds_buy_must_exceed_sell():
    with pytest.raises(ValidationError):
        Thresholds(buy=-0.1, sell=0.1)


def test_fund_code_pattern():
    with pytest.raises(ValidationError):
        FundSpec(code="abc", name="x")


def test_base_url_endpoint_truncated():
    llm = LLMSettings(base_url="https://x.cn/v1/chat/completions", model="m", api_key="k")
    assert llm.base_url == "https://x.cn/v1"


def test_duplicate_fund_codes_rejected():
    with pytest.raises(ValidationError):
        AppConfig(
            funds=[FundSpec(code="110022", name="a"), FundSpec(code="110022", name="b")],
            llm=LLMSettings(model="m", api_key="k"),
            db_path="/tmp/x.db",
        )


def test_settings_flat_properties():
    s = Settings()
    assert s.max_single_fund_position == s.risk.max_single_fund_position
    assert s.cash_buffer == s.risk.cash_buffer
