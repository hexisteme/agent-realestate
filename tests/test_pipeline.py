"""스코어링·시나리오·G3 재현성·assembler 테스트."""
from datetime import date

from agent_realestate.analysts.finance import build_finance_plan
from agent_realestate.analysts.redev import score_redev
from agent_realestate.analysts.risk import assess_flags, penalty_product, has_hard_fail
from agent_realestate.analysts.scoring import AXES, WEIGHTS, score_candidates
from agent_realestate.analysts.sensitivity import sensitivity_analysis
from agent_realestate.domain import (Candidate, DataSource, ExitStrategy, Listing,
                                     PriceKind, RedevStage)
from agent_realestate.synthesis.assembler import Evaluated, build_report
from agent_realestate.analysts.finance import build_finance_plan
from agent_realestate.synthesis.scenario import compute_hold, project_networth_15yr


def _cand(name, price, area, units, far, land, stage, jeonse, transit):
    l = Listing(complex_name=name, dong_ho="101동 101호", area_exclusive_m2=area,
                floor="5/15층", facing="남향", price_krw=price, price_kind=PriceKind.ASKING_LIVE,
                agent_name="공인", confirmed_date=date(2026, 5, 27), source=DataSource.NAVER_LIVE_CHROME)
    return Candidate(listing=l, units=units, built_year=1989, far_pct=far, land_share_pyeong=land,
                     land_share_is_estimate=True, redev_stage=stage, jeonse_krw=jeonse,
                     transit=transit, district="노원구")


def _sample():
    return [
        _cand("상계주공3", 830_000_000, 59, 2213, 180, 13, RedevStage.SAFETY_PASS, 400_000_000, "노원역 7호선·GTX-C 창동"),
        _cand("청량리신현대", 920_000_000, 60, 736, 204, 9.5, RedevStage.NONE, 450_000_000, "회기역·GTX-C 청량리"),
    ]


def test_weights_sum_to_one():
    for w in WEIGHTS.values():
        assert abs(sum(w.values()) - 1.0) < 1e-9
        assert set(w) == set(AXES)


def test_scoring_redev_winner():
    cs = _sample()
    redevs = [score_redev(c) for c in cs]
    axes = score_candidates(cs, redevs, ExitStrategy.HOLD_AND_RENT)
    # 재건축 단계 있는 상계주공3 의 상승여력 점수가 단계 NONE 청량리보다 높아야
    s3 = next(a for a in axes if a.candidate.listing.complex_name == "상계주공3")
    cn = next(a for a in axes if a.candidate.listing.complex_name == "청량리신현대")
    assert s3.scores["상승여력"] > cn.scores["상승여력"]


def test_hold_residual_loan():
    h = compute_hold(price_krw=830_000_000, loan_krw=581_000_000, jeonse_krw=400_000_000,
                     property_tax_krw=1_500_000, rate=0.043)
    assert h.residual_loan_krw == 181_000_000           # 5.81억 - 4억
    assert h.extra_cash_to_convert_krw == 181_000_000
    assert len(h.rows) == 9                              # 3 연수 × 3 상승가정


def _build():
    cs = _sample()
    redevs = [score_redev(c) for c in cs]
    axes = score_candidates(cs, redevs, ExitStrategy.HOLD_AND_RENT)
    ev = []
    for c, r, a in zip(cs, redevs, axes):
        fin = build_finance_plan(price_krw=c.listing.price_krw, ltv_ratio=0.70,
                                 annual_income_krw=100_000_000, own_capital_krw=420_000_000,
                                 rate=0.043, term_years=40, first_time=True,
                                 area_exclusive_m2=c.listing.area_exclusive_m2)
        h = compute_hold(price_krw=c.listing.price_krw, loan_krw=fin.loan_krw,
                         jeonse_krw=c.jeonse_krw or 0, property_tax_krw=fin.property_tax_krw, rate=0.043)
        flags = tuple(assess_flags(c, ExitStrategy.HOLD_AND_RENT))
        adj = round(a.weighted_total * penalty_product(list(flags)), 3)
        ev.append(Evaluated(candidate=c, finance=fin, redev=r, axis=a, hold=h, break_even=None,
                            flags=flags, adjusted_total=adj))
    return build_report(profile={"exit_strategy": "HOLD_AND_RENT", "연소득": "1억"},
                        strategy=ExitStrategy.HOLD_AND_RENT, evaluated=ev, policies=[],
                        today=date(2026, 5, 27), council_insight=_insight)


_insight = None


def test_council_insight_section():
    global _insight
    _insight = "장기보유라면 재건축 진행 단지가 우위"
    try:
        html = _build()
    finally:
        _insight = None
    assert "참조 2 — Council 통합통찰" in html   # 원문 인용은 참조로 강등
    assert "장기보유라면 재건축 진행 단지가 우위" in html
    assert "정성 보조" in html   # council 원문과 Claude 판단 분리 문구
    assert "Council 통찰" in html   # 본문 1줄 헤드라인 잔류


def test_g3_reproducible():
    # 동일 입력 → 동일 산출 (turing 재현성 가설, G3)
    assert _build() == _build()


def _ev_for_compset():
    cs = _sample()
    redevs = [score_redev(c) for c in cs]
    axes = score_candidates(cs, redevs, ExitStrategy.HOLD_AND_RENT)
    ev = []
    for c, r, a in zip(cs, redevs, axes):
        fin = build_finance_plan(price_krw=c.listing.price_krw, ltv_ratio=0.70,
                                 annual_income_krw=100_000_000, own_capital_krw=420_000_000,
                                 rate=0.043, term_years=40, first_time=True,
                                 area_exclusive_m2=c.listing.area_exclusive_m2)
        ev.append(Evaluated(candidate=c, finance=fin, redev=r, axis=a, hold=None, break_even=None,
                            flags=(), adjusted_total=a.weighted_total))
    return ev


# band-aware 스키마(2026-06-02): _sample 매물 전용 59/60 → bucket 59. longrun "노원-상계|59".
_COMPSET = {
    "_meta": {"baseline": "테스트 동일단지 실현 CAGR"},
    "assign": {"상계주공3": "노원-상계", "청량리신현대": "노원-상계"},   # 후보 saenghwalgwon="" → assign 폴백
    "longrun": {"노원-상계|59": [0.04, 0.05, 0.06, 0.07, 0.05, 0.06, 0.07, 0.08]},  # n=8 (게이트 통과)
    "recent": {"노원-상계|상계주공3|59": 0.02, "노원-상계|청량리신현대|59": 0.06},   # "생활권|단지|band"
    "peers_recent": {"노원-상계|59": [0.02, 0.06, 0.04, 0.05]},
}


def test_three_signals_render_with_compset():
    html = build_report(profile={"exit_strategy": "HOLD_AND_RENT", "own_capital_krw": 420_000_000},
                        strategy=ExitStrategy.HOLD_AND_RENT, evaluated=_ev_for_compset(),
                        policies=[], today=date(2026, 5, 27), compset=_COMPSET)
    # ① base-rate 밴드(주신호) median +6.0%/년 (n=8 게이트 통과)
    assert "생활권 base-rate 밴드 (주신호)" in html
    assert "노원-상계" in html and "+6.0%" in html
    # ② mean-reversion: 상계주공3(recent 0.02, 최저) = 저평가 / 청량리(0.06, 최고) = 고평가
    assert "저평가(반등여지)" in html and "고평가(과열주의)" in html
    # base-rate 미주입(필터only) 경로도 깨지지 않음
    nofill = build_report(profile={"exit_strategy": "HOLD_AND_RENT", "own_capital_krw": 420_000_000},
                          strategy=ExitStrategy.HOLD_AND_RENT, evaluated=_ev_for_compset(),
                          policies=[], today=date(2026, 5, 27), compset=None)
    assert "필터only" in nofill


def test_networth_15yr_band_and_no_transfer():
    # 매각가치 = price×(1+g)^15. 보수 0%/중립 CPI/낙관 base-rate. 순자산 = 매각−대출−보유비용−기회비용.
    nw = project_networth_15yr(name="A", saenghwalgwon="구로-신도림", price_krw=1_000_000_000,
                               residual_debt_krw=300_000_000, annual_carry_krw=10_000_000,
                               equity_krw=400_000_000, base_rate_median=0.07,
                               opportunity_rate=0.035, cpi=0.023, legal_status="PASS")
    assert nw.baserate_injected is True
    assert nw.g_band_pct == (0.0, 2.3, 7.0)
    assert nw.sale_lo_krw == 1_000_000_000              # (1+0)^15
    assert nw.sale_hi_krw == int(1e9 * 1.07 ** 15)
    assert nw.cumulative_carry_krw == 150_000_000       # 10m × 15
    assert nw.opportunity_cost_krw == int(4e8 * (1.035 ** 15 - 1))   # ★복리(3차 감사 E) — 매각가치와 동일 기하
    assert nw.net_lo_krw < nw.net_mid_krw < nw.net_hi_krw
    assert nw.net_mid_krw == nw.sale_mid_krw - 300_000_000 - 150_000_000 - nw.opportunity_cost_krw
    # 전이 금지: base-rate 미주입(None)이면 낙관 g 를 중립(CPI)으로 캡 → 과대평가 방지
    nw2 = project_networth_15yr(name="B", saenghwalgwon="수성-범어", price_krw=1_000_000_000,
                                residual_debt_krw=300_000_000, annual_carry_krw=10_000_000,
                                equity_krw=400_000_000, base_rate_median=None)
    assert nw2.baserate_injected is False
    assert nw2.g_band_pct[2] == nw2.g_band_pct[1]       # 낙관 = 중립(캡)


def test_report_has_networth_compare_section():
    html = build_report(profile={"exit_strategy": "HOLD_AND_RENT", "own_capital_krw": 420_000_000},
                        strategy=ExitStrategy.HOLD_AND_RENT, evaluated=_ev_for_compset(),
                        policies=[], today=date(2026, 5, 27), compset=_COMPSET)
    assert "§7. A′ vs B" in html and "순자산 단일화폐" in html
    assert "비교셋-초월 비교의 유일한 정직한 단위" in html
    # 보유기간 심화(2026-06-02): 10·15·20년 컬럼
    assert "순자산 10년" in html and "순자산 15년" in html and "순자산 20년" in html


def test_baserate_nmin_gate_falls_back_to_filter_only():
    # longrun n<8 → base_rate_band None → 필터only 라벨 (편향-분산 가드, falsifier #2)
    cs = dict(_COMPSET, longrun={"노원-상계|59": [0.05, 0.06, 0.07]})  # n=3
    html = build_report(profile={"exit_strategy": "HOLD_AND_RENT", "own_capital_krw": 420_000_000},
                        strategy=ExitStrategy.HOLD_AND_RENT, evaluated=_ev_for_compset(),
                        policies=[], today=date(2026, 5, 27), compset=cs)
    assert "필터only" in html


def test_residence_conflict_guard():
    # 상계주공5: 관리처분(MGMT_DISPOSAL)·전세없음 → HOLD_AND_RENT 에서 F_MOVEOUT 플래그·강등
    sangye5 = _cand("상계주공5", 700_000_000, 31, 996, 93, 18, RedevStage.MGMT_DISPOSAL, None,
                    "노원역 도보5분·GTX-C 창동")
    flags = assess_flags(sangye5, ExitStrategy.HOLD_AND_RENT)
    assert any(f.code == "F_MOVEOUT" for f in flags)
    assert penalty_product(flags) < 1.0
    # 거주 가능 단지(상계주공3)는 플래그 없음
    s3 = _cand("상계주공3", 830_000_000, 59, 2213, 180, 13, RedevStage.SAFETY_PASS, 400_000_000, "노원역 7호선")
    assert assess_flags(s3, ExitStrategy.HOLD_AND_RENT) == []
    # PRIMARY_ONLY(거주 필요)도 강등, but 매도 안 하는 전략 외엔 거주 전제이므로 모두 적용
    assert any(f.code == "F_MOVEOUT" for f in assess_flags(sangye5, ExitStrategy.PRIMARY_ONLY))


def test_toher_rent_flag():
    """★4차 감사 A 재수정(2026-06-13): 서울 전역 아파트 토허(2025.10.20~2026.12.31).
    서울 district 자동감지 + toher_zone 수동 override 두 경로 모두 검증."""
    from dataclasses import replace
    # ① toher_zone=True 수동 — 비서울 후보도 발화 (경기 등 추가 지정 시 사용)
    base = _cand("강남아파트", 1_500_000_000, 84, 500, 200, 15, RedevStage.NONE, 800_000_000, "역삼역 2호선")
    toher = replace(base, toher_zone=True)
    flags = assess_flags(toher, ExitStrategy.HOLD_AND_RENT)
    assert any(f.code == "F_TOHER_RENT" for f in flags)
    assert all(f.penalty == 1.0 for f in flags if f.code == "F_TOHER_RENT")  # soft — 순위 무변
    assert not has_hard_fail(flags)
    # 다른 전략에선 미발생(서울 아님 + toher_zone=True)
    assert not any(f.code == "F_TOHER_RENT" for f in assess_flags(toher, ExitStrategy.LIVE_THEN_SELL))
    assert not any(f.code == "F_TOHER_RENT" for f in assess_flags(toher, ExitStrategy.PRIMARY_ONLY))
    # 비서울 + toher_zone=False → 미발생
    assert not any(f.code == "F_TOHER_RENT" for f in assess_flags(base, ExitStrategy.HOLD_AND_RENT))
    # ② 서울 district 자동감지 — toher_zone=False 이더라도 서울이면 발화
    from agent_realestate.domain import Listing, PriceKind, DataSource
    from datetime import date
    _listing_seoul = Listing(
        complex_name="강남아파트", dong_ho="101동 301호", area_exclusive_m2=84,
        floor="3/15층", facing="남향", price_krw=1_500_000_000,
        price_kind=PriceKind.ASKING_LIVE, agent_name="테스트공인", confirmed_date=date(2026, 6, 13),
        source=DataSource.NAVER_LIVE_CHROME,
    )
    from agent_realestate.domain import Candidate, RedevStage as RS
    seoul_cand = Candidate(
        listing=_listing_seoul, units=500, built_year=2000, far_pct=250,
        land_share_pyeong=10.0, land_share_is_estimate=False, redev_stage=RS.NONE,
        jeonse_krw=800_000_000, transit="역삼역 2호선", district="서울 강남구",
        toher_zone=False,  # 명시적 False — district 자동감지로 발화해야 함
    )
    seoul_flags = assess_flags(seoul_cand, ExitStrategy.HOLD_AND_RENT)
    assert any(f.code == "F_TOHER_RENT" for f in seoul_flags)  # 서울 자동감지
    assert not has_hard_fail(seoul_flags)


def test_ten_axes_primary_scoring_matrix():
    # 2026-06-03(사용자 OVERRIDE): 10축 가중점수 매트릭스 = 리포트 본문 주 비교 프레임(별지 D 폐기·deprecated 되돌림).
    cs = _sample()
    redevs = [score_redev(c) for c in cs]
    axes = score_candidates(cs, redevs, ExitStrategy.HOLD_AND_RENT)
    assert "토지지분" in AXES and "출퇴근" in AXES and "학군" in AXES and len(AXES) == 10
    s3 = next(a for a in axes if a.candidate.listing.complex_name == "상계주공3")
    cn = next(a for a in axes if a.candidate.listing.complex_name == "청량리신현대")
    # 토지지분 feature 추출 동작(상계주공3 land 13평 > 청량리 9.5평)
    assert s3.scores["토지지분"] > cn.scores["토지지분"]
    # cbd_km 미주입 후보는 출퇴근 중립 3.0
    assert s3.scores["출퇴근"] == 3.0
    # 리포트에서 10축 점수는 본문 주 프레임(별지·deprecated 아님)
    html = _build()
    assert "§5. 10축 가중점수 — 주 비교 프레임" in html
    assert "이 표가 주 비교 프레임이다" in html
    assert "별지 D" not in html              # 10축이 별지로 강등돼 있지 않음


def test_sensitivity_covers_inputs_and_weights():
    cs = _sample()
    flags = [() for _ in cs]
    rows = sensitivity_analysis(cs, ExitStrategy.HOLD_AND_RENT, flags, base_top="상계주공3")
    # ★3차 감사 A: 가중치 교란 4행(상위 2축 ±30%)만 — 매매가/전세 교란은 호가무관 순위에 inert(가짜 robust)라 제거
    assert len(rows) == 4
    assert all(r.label and r.new_top for r in rows)
    assert all("가중치" in r.label for r in rows)   # 진짜 구속 변수(가중치)만 교란
    # 유효 경쟁자 ≤1 이면 검정 무의미 공시 행이 추가된다
    rows1 = sensitivity_analysis(cs[:1], ExitStrategy.HOLD_AND_RENT, [()], base_top=cs[0].listing.complex_name)
    assert any("검정 무의미" in r.label for r in rows1)


def test_stress_dsr_more_conservative():
    p = build_finance_plan(price_krw=830_000_000, ltv_ratio=0.70, annual_income_krw=100_000_000,
                           own_capital_krw=420_000_000, rate=0.043, term_years=40,
                           first_time=True, area_exclusive_m2=59)
    assert p.dsr_loan_stress_krw < p.dsr_loan_krw   # 가산금리로 한도 ↓
    assert p.loan_krw == min(p.ltv_loan_krw, p.dsr_loan_stress_krw)


def test_hold_carry_extras_and_cagr():
    h0 = compute_hold(price_krw=830_000_000, loan_krw=581_000_000, jeonse_krw=400_000_000,
                      property_tax_krw=1_500_000, rate=0.043)
    h_extras = compute_hold(price_krw=830_000_000, loan_krw=581_000_000, jeonse_krw=400_000_000,
                            property_tax_krw=1_500_000, rate=0.043,
                            management_fee_annual_krw=1_000_000, maintenance_annual_krw=2_500_000,
                            monthly_rent_krw=0, vacancy_pct=0.03)
    assert h_extras.annual_carry_krw == h0.annual_carry_krw + 1_000_000 + 2_500_000
    # 회귀 밴드 주입 시 lo/mid/hi 가 그대로 상승률로 적용 (점예측 폐기)
    h_band = compute_hold(price_krw=830_000_000, loan_krw=581_000_000, jeonse_krw=400_000_000,
                          property_tax_krw=1_500_000, rate=0.043, growth_band=(0.03, 0.05, 0.07))
    # ★장기 캡(3차 감사 2026-06-11): 5년 행은 단기 회귀밴드 원본, 10/15년 행은 ±4%/년 지속가능 캡
    apps5 = sorted({round(r.appreciation, 4) for r in h_band.rows if r.years == 5})
    assert apps5 == [0.03, 0.05, 0.07]
    apps15 = sorted({round(r.appreciation, 4) for r in h_band.rows if r.years == 15})
    assert apps15 == [0.03, 0.04]
    assert h_band.band_kind == "회귀밴드"
    # 밴드 None → 보수 중립 밴드(-2/0/+2%)로 대체 (추세 약함/없음)
    h_neutral = compute_hold(price_krw=830_000_000, loan_krw=581_000_000, jeonse_krw=400_000_000,
                             property_tax_krw=1_500_000, rate=0.043, growth_band=None)
    assert h_neutral.band_kind == "보수중립"
    assert sorted({round(r.appreciation, 4) for r in h_neutral.rows}) == [-0.02, 0.0, 0.02]


def test_norent_flag():
    # 전세 없음 + 일반단계 + HOLD_AND_RENT → 약한 임대수요 플래그
    no_jeonse = _cand("전세없는단지", 800_000_000, 59, 800, 200, 8, RedevStage.NONE, None, "역세권")
    assert any(f.code == "F_NORENT" for f in assess_flags(no_jeonse, ExitStrategy.HOLD_AND_RENT))


def test_report_has_sections():
    html = _build()
    # 2026-06-03(사용자 OVERRIDE): §5 10축이 주 프레임. §★ 보조 FACT 신호는 거시 보조로 강등(삭제 아님).
    assert "§5. 10축 가중점수 — 주 비교 프레임" in html       # 주 프레임
    assert "§★ 보조 FACT 신호" in html                        # 보조로 강등된 신호(보존)
    assert "적합도 facts + 하드필터" in html                  # ③ 신호(보존)
    assert "🥇" in html and "이 우선순위를 뒤집는 조건" in html  # BLUF
    assert "10축 조정점수" in html                            # BLUF/비교 매트릭스 조정점수
    assert "후보 비교" in html                                # 비교 매트릭스
    # 본문 잔류 섹션
    for sec in ("§1", "§3", "§4", "§5", "§6"):
        assert sec in html
    # 별지/참조로 강등됐으나 내용은 보존(삭제 0). 별지 A는 신뢰도/민감도 데이터 있을 때만(조건부)
    assert "별지 B — 5축 방법론" in html
    assert "별지 C — 한계와 자기진단" in html
    assert "참조 1 — 정책 스냅샷" in html
    assert "참조 3 — 다음 의사결정 질문" in html
    # 내용 보존 검증: 면책·자기진단·정직 프레이밍 텍스트가 사라지지 않음
    assert "재현성" in html and "미래 적중률" in html          # 옛 §10 한계 전문
    assert "전 매물 4요소·NAVER_LIVE_CHROME 검증 ✓" in html    # 옛 §9 자기진단
    assert "후보 1~2개로 좁히면" in html                        # 옛 §11 다음 질문
    assert "NAVER_LIVE_CHROME" in html


def test_merge_axis_weights_override():
    # 범용화(2026-06-12): profile axis_weights 부분 override — 병합+재정규화(합=1), 미지 축은 거부
    from agent_realestate.analysts.scoring import merge_axis_weights
    assert merge_axis_weights(ExitStrategy.HOLD_AND_RENT, None) is None
    w = merge_axis_weights(ExitStrategy.HOLD_AND_RENT, {"학군": 0.30})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    base = merge_axis_weights(ExitStrategy.HOLD_AND_RENT, {})
    assert base is None  # 빈 dict = override 없음
    assert w["학군"] > 0.20          # 0.08 → 0.30 주입 후 재정규화돼도 크게 상승
    import pytest
    with pytest.raises(SystemExit):
        merge_axis_weights(ExitStrategy.HOLD_AND_RENT, {"없는축": 0.5})
