"""compset_signals (재편 A 3신호) 단위 테스트 — 결정론·경계·억제 검증."""
from agent_realestate.analysts.compset_signals import (
    band_from_cagrs, base_rate_band, classify_mean_reversion,
    mean_reversion_signal, fitness_facts,
)


def test_band_quartiles():
    lo, med, hi = band_from_cagrs([0.01, 0.03, 0.05, 0.07, 0.09])
    assert lo < med < hi
    assert med == 0.05


def test_base_rate_nmin_gate():
    # n_min 미만이면 None (필터only 강등)
    assert base_rate_band("구로-신도림", 84, [0.05, 0.06], n_min=8) is None
    b = base_rate_band("구로-신도림", 84, [0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.07, 0.06], n_min=8)
    assert b is not None and b.n == 8 and b.saenghwalgwon == "구로-신도림"


def test_mean_reversion_contrarian_labels():
    # 역발상: 하위 백분위(최근 덜 오름) = 저평가(반등여지)
    assert classify_mean_reversion(0.1) == "저평가(반등여지)"
    assert classify_mean_reversion(0.5) == "중립"
    assert classify_mean_reversion(0.9) == "고평가(과열주의)"


def test_mean_reversion_signal_position():
    peers = [0.02, 0.04, 0.06, 0.08, 0.10]
    # 최근 가장 적게 오른 단지 → 저백분위 → 저평가
    s = mean_reversion_signal("A", 0.01, peers)
    assert s is not None and s.percentile == 0.0 and s.label == "저평가(반등여지)"
    # 가장 많이 오른 단지 → 고평가
    s2 = mean_reversion_signal("B", 0.12, peers)
    assert s2.label == "고평가(과열주의)"


def test_mean_reversion_suppressed_small_peer():
    assert mean_reversion_signal("A", 0.05, [0.04, 0.05, 0.06]) is None  # peer<4 억제


def test_fitness_facts_hardgate():
    f = fitness_facts(equity_required_krw=int(3.8e8), own_capital_krw=int(4.0e8),
                      units=2400, jeonse_ratio=0.58, slope_pct=4.0, legal_status="조건부")
    assert f["예산적합"] is True and f["유동성_등급"] == "상" and f["경사_등급"] == "완경사"
    f2 = fitness_facts(equity_required_krw=int(4.5e8), own_capital_krw=int(4.0e8),
                       units=300, jeonse_ratio=None, slope_pct=None, legal_status="FAIL")
    assert f2["예산적합"] is False and f2["전세가율"] is None and f2["경사_등급"] is None


def test_determinism():
    args = dict(equity_required_krw=int(3.8e8), own_capital_krw=int(4.0e8),
                units=1200, jeonse_ratio=0.55, slope_pct=7.0, legal_status="PASS")
    assert fitness_facts(**args) == fitness_facts(**args)
