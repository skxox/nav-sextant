"""IC 自适应调权测试：检验结论 → 权重乘数/方向，及其在合成层的落地。"""
from __future__ import annotations

from backend.agents.base import AgentOutput
from backend.config import Settings
from backend.engine.factor_eval import FactorICReport, ic_weight_adjustments
from backend.engine.orchestrator import synthesize


def _report(factor: str, samples: int = 100, rank_ic: float = 0.3,
            t: float = 3.0) -> FactorICReport:
    return FactorICReport(
        factor=factor, label=f"{factor}·测试", samples=samples, rank_ic=rank_ic,
        ic_tstat=t, ic_mean=rank_ic, ic_std=0.1, icir=rank_ic / 0.1, ic_win_rate=0.6,
        q5_q1_spread_pct=1.0, forward_days=5,
        verdict="显著有效" if t >= 2 else "方向正确但不显著",
    )


# ---------- ic_weight_adjustments 四类结论 ----------

def test_significant_reverse_flips_sign_and_downweights():
    adj = ic_weight_adjustments([_report("momentum", rank_ic=-0.23, t=-2.5)])
    assert adj["technical"].sign == -1
    assert adj["technical"].mult == 0.7
    assert "显著反向" in adj["technical"].note


def test_significant_effective_gets_bonus():
    adj = ic_weight_adjustments([_report("momentum", rank_ic=0.28, t=3.1)])
    assert adj["technical"].mult == 1.2
    assert adj["technical"].sign == 1


def test_reverse_but_insignificant_downweights_half():
    adj = ic_weight_adjustments([_report("momentum", rank_ic=-0.1, t=-1.2)])
    assert adj["technical"].mult == 0.5
    assert adj["technical"].sign == 1  # 不反向，只降权


def test_correct_direction_untouched():
    assert ic_weight_adjustments([_report("momentum", rank_ic=0.1, t=1.2)]) == {}


def test_min_samples_gate():
    # 样本不足（< min_samples）的因子不参与调权
    assert ic_weight_adjustments([_report("momentum", samples=29, rank_ic=-0.9, t=-9.0)],
                                 min_samples=30) == {}


def test_multiple_factor_families_pick_max_abs_t():
    # 估值族两路（valuation / valuation_proxy）→ 取 |t| 大者
    reports = [
        _report("valuation", rank_ic=-0.05, t=-0.8),
        _report("valuation_proxy", rank_ic=-0.3, t=-2.2),
    ]
    adj = ic_weight_adjustments(reports)
    assert adj["valuation"].factor == "valuation_proxy"
    assert adj["valuation"].sign == -1


# ---------- synthesize 落地：mult 降权 + sign 反向 ----------

def _outputs() -> dict[str, AgentOutput]:
    return {
        "technical": AgentOutput(agent="technical", score=0.6, direction="BUY",
                                 confidence=0.5, summary="动量多头"),
        "valuation": AgentOutput(agent="valuation", score=-0.6, direction="SELL",
                                 confidence=0.5, summary="估值高位"),
    }


def _settings() -> Settings:
    from backend.config import Weights
    return Settings(weights=Weights(technical=0.5, valuation=0.5,
                                    sentiment=0.0, risk=0.0))


def test_synthesize_baseline_without_adjustments():
    sig = synthesize("161725", "白酒", "2026-11-09", _outputs(), _settings())
    # 两个等权等置信 Agent：+0.6 与 -0.6 相互抵消
    assert sig.composite_score == 0.0


def test_synthesize_mult_downweights_agent():
    # 技术面 ×0.5 → 合成分向估值面倾斜
    sig = synthesize("161725", "白酒", "2026-11-09", _outputs(), _settings(),
                     ic_adjustments={"technical": {"mult": 0.5, "sign": 1, "note": "x"}})
    assert sig.composite_score < 0
    # 等置信度下：eff_w 技术面 0.25 / 估值面 0.5 → (0.25*0.6 - 0.5*0.6)/0.75 = -0.2
    assert abs(sig.composite_score - (-0.2)) < 1e-9


def test_synthesize_sign_flip_reverses_contribution():
    # 技术面显著反向 → sign=-1：+0.6 贡献变 -0.6，合成分 = -0.6
    sig = synthesize("161725", "白酒", "2026-11-09", _outputs(), _settings(),
                     ic_adjustments={"technical": {"mult": 1.0, "sign": -1, "note": "反向使用"}})
    assert abs(sig.composite_score - (-0.6)) < 1e-9
    assert any(r.startswith("[ic] 反向使用") for r in sig.reasons)


def test_synthesize_appends_ic_note_to_reasons():
    sig = synthesize("161725", "白酒", "2026-11-09", _outputs(), _settings(),
                     ic_adjustments={"valuation": {"mult": 1.2, "sign": 1,
                                                   "note": "估值分位 显著有效 → ×1.2"}})
    assert "[ic] 估值分位 显著有效 → ×1.2" in sig.reasons
