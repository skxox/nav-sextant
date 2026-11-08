"""共享测试夹具：合成净值/估值数据，无网络依赖。"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import AppConfig, FundSpec, LLMSettings, Settings


def make_nav_series(n: int = 200, drift: float = 0.0005, vol: float = 0.01, seed: int = 42) -> pd.DataFrame:
    """几何随机游走净值序列。"""
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, vol, n)
    nav = 1.0 * np.cumprod(1 + rets)
    dates = pd.bdate_range("2025-01-01", periods=n).strftime("%Y-%m-%d")
    return pd.DataFrame({"trade_date": dates, "nav": nav})


def make_pe_series(n: int = 750, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    pe = 12 + 4 * np.sin(np.linspace(0, 6, n)) + rng.normal(0, 0.5, n)
    dates = pd.bdate_range("2022-01-01", periods=n).strftime("%Y-%m-%d")
    return pd.DataFrame({"trade_date": dates, "pe": pe, "pb": pe / 4})


@pytest.fixture
def nav_df() -> pd.DataFrame:
    return make_nav_series()


@pytest.fixture
def pe_df() -> pd.DataFrame:
    return make_pe_series()


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        funds=[FundSpec(code="110022", name="测试基金", benchmark_index="000300")],
        settings=Settings(),
        llm=LLMSettings(model="test-model", api_key="test-key"),
        db_path=tmp_path / "test.db",
    )
