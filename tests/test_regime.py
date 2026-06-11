"""사이클 국면(regime) 분류 모듈 — 결정론·타임라인 정합 테스트 (2026-06-04 정량×정성 연결)."""
from agent_realestate.analysts.regime import (classify_regime, compute_regime,
                                              current_regime,
                                              regime_conditional_guidance,
                                              regime_leading_inputs,
                                              PHASE_VOLUME_IC)


def test_timeline_phases():
    assert classify_regime(2013).phase == "BOTTOM"
    assert classify_regime(2015).phase == "ASCENDING"
    assert classify_regime(2018).phase == "OVERHEATED"
    assert classify_regime(2023).phase == "CORRECTING"
    assert classify_regime(1990) is None       # 범위 밖


def test_ascending_activates_volume_signal():
    # 상승진입: 거래량 양(+)·프리미엄 momentum(+). feature_discovery 정합.
    a = classify_regime(2015)
    assert a.volume_signal == 1 and a.value_signal == 1
    # 과열: 거래량 소멸(0)·저평가 reversion(-)
    o = classify_regime(2019)
    assert o.volume_signal == 0 and o.value_signal == -1


def test_current_regime_is_latest():
    c = current_regime()
    assert c.year == 2025 and c.phase == "OVERHEATED"   # 2025 재과열·재긴축


def test_phase_volume_ic_matches_finding():
    # 발견 수치(in-sample within-구 거래량 IC)와 라벨 정합
    assert PHASE_VOLUME_IC["ASCENDING"] > PHASE_VOLUME_IC["BOTTOM"] > PHASE_VOLUME_IC["OVERHEATED"]


def test_leading_inputs_separate_coincident():
    li = regime_leading_inputs(2015)
    assert "금리" in li["leading"] and "정책" in li["leading"]      # 선행 식별자
    assert "심리" in li["coincident_confirm"]                       # 후행 확인 분리


def test_compute_regime_quant_inputs():
    # 정량 입력으로 도출 — 관측가능 신호. 과열(고모멘텀), 긴축+중모멘텀=과열 조기포착.
    assert compute_regime(2019, 0.18) == "OVERHEATED"            # 고모멘텀
    assert compute_regime(2017, 0.10) == "OVERHEATED"            # 중모멘텀+긴축(정책 선행)
    assert compute_regime(2015, 0.05) == "ASCENDING"             # 회복+완화
    assert compute_regime(2013, -0.02) == "BOTTOM"               # 저모멘텀+완화
    assert compute_regime(2022, 0.12, prev_mom=0.17) == "CORRECTING"   # 급속인상(rate_dir>1.5)
    assert compute_regime(2023, -0.015, prev_mom=0.19) == "CORRECTING" # 고→음 전환


def test_compute_regime_unsold_distinguishes_correcting():
    # 미분양 급증(+급증) → BOTTOM 아닌 CORRECTING (도착 데이터 plug-in 경로)
    assert compute_regime(2013, -0.02, unsold_trend=0.3) == "CORRECTING"


def test_compute_regime_fallback_timeline():
    assert compute_regime(2018, None) == "OVERHEATED"           # market_mom 부재 → _TIMELINE


def test_unsold_surge_in_rising_market_stays_ascending():
    # ★2015 사례: 미분양 급증(+52%)이나 가격 상승기(mom+4.6%) → CORRECTING 아닌 ASCENDING(fragile override 방지)
    assert compute_regime(2015, 0.046, unsold_trend=0.52) == "ASCENDING"
    # 모멘텀 약(<2%) + 미분양 상승 → CORRECTING
    assert compute_regime(2024, -0.002, unsold_trend=0.12) == "CORRECTING"


def test_regime_inputs_real_data_loaded():
    from agent_realestate.analysts.regime import load_regime_inputs, unsold_trend, regime_evidence
    inp = load_regime_inputs()
    assert inp.get("unsold", {}).get("2010") == 88706          # 실데이터 고점(e-나라지표)
    assert inp["unsold"]["2021"] == 17710                       # 저점
    assert unsold_trend(2022, inp) > 2.0                        # 2022 급증(+285%)
    ev = regime_evidence()
    assert ev["unsold_peak"][0] == "2010" and ev["unsold_trough"][0] == "2021"


def test_guidance_deterministic_and_labeled():
    g = regime_conditional_guidance(2015)
    assert "ASCENDING" in g and "OOS 제한" in g                     # 예측기 아님 라벨 강제
    assert regime_conditional_guidance(2015) == g                   # 결정론


def test_sentiment_confirms_coincident():
    from agent_realestate.analysts.regime import sentiment_confirms, sentiment_annual
    # 2025 OVERHEATED + 소비심리 128.6 → 과열 확인 (driver 아닌 confirmation)
    st, v = sentiment_confirms("OVERHEATED", 2025)
    assert v is not None and v >= 125 and "확인" in st
    # 2022 CORRECTING + 심리 98.7 → 공포/위축 확인
    st2, v2 = sentiment_confirms("CORRECTING", 2022)
    assert v2 < 110 and "확인" in st2
    assert sentiment_annual(2015) > 130       # 상승기 고심리(실데이터)


def test_jeonse_value_timing():
    from agent_realestate.analysts.regime import jeonse_value_timing
    jv = jeonse_value_timing()
    assert jv["ratio"] > 0 and 0 <= jv["pct"] <= 1
    # 2025 서울 53.4% = 역사 저점 근접 → value 불리
    assert "불리" in jv["signal"]
    # 2016 고점(71.7) → value 유리
    assert "유리" in jeonse_value_timing(2016)["signal"]


def test_regime_entry_read_combines_phase_and_jeonse():
    from agent_realestate.analysts.regime import regime_entry_read
    # 2025 과열 + 전세가율 저(53.4%) = 갭주도 무지지 → 고위험
    er = regime_entry_read()
    assert er["phase"] == "OVERHEATED" and er["supported"] is False and er["risk"] == "고"
    # 2015 상승진입 + 전세가율 고/상승 = 전세지지 → 저위험 양호
    er15 = regime_entry_read(2015)
    assert er15["phase"] == "ASCENDING" and er15["supported"] is True and er15["risk"] == "저"
