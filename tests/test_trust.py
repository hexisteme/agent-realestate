"""결정신뢰도 지표 테스트 (입력 검증 완성도)."""
from datetime import date

from agent_realestate.analysts.trust import assess_trust
from agent_realestate.analysts.trend import compute_trend
from agent_realestate.domain import Candidate, DataSource, Listing, PriceKind, RedevStage


def _cand(price=830_000_000, land_estimate=True):
    li = Listing(complex_name="상계주공3", dong_ho="324동 506호", area_exclusive_m2=59,
                 floor="5/15층", facing="동향", price_krw=price, price_kind=PriceKind.ASKING_LIVE,
                 agent_name="노원공인", confirmed_date=date(2026, 5, 27), source=DataSource.NAVER_LIVE_CHROME)
    return Candidate(listing=li, units=2213, built_year=1987, far_pct=180, land_share_pyeong=13,
                     land_share_is_estimate=land_estimate, redev_stage=RedevStage.SAFETY_PASS,
                     jeonse_krw=400_000_000, transit="노원역 7호선·창동 GTX-C 인접", district="노원구")


def test_trust_low_when_all_manual():
    # 추세 없음·정책 기본값·대지지분 추정·입지신호 없음 → 낮은 신뢰
    t = assess_trust(_cand(), trend=None, policy_is_default=True, has_location_signal=False)
    assert t.score_pct < 60
    assert t.grade == "참고만"
    assert any("호가" in b for b in t.blocking)


def test_trust_rises_with_verification():
    # 호가↔실거래 괴리 작음(교차검증) + 강한 추세 + 등기부 실측 + 정책검증 + 입지검증
    series = [{"deal_ym": f"2025-{m:02d}", "price_krw": p} for m, p in
              [(1, 800_000_000), (3, 805_000_000), (5, 812_000_000), (7, 820_000_000),
               (9, 828_000_000), (11, 835_000_000), (12, 840_000_000), (12, 842_000_000)]]
    tr = compute_trend(series)
    t = assess_trust(_cand(price=840_000_000, land_estimate=False), trend=tr,
                     policy_is_default=False, has_location_signal=True)
    assert t.score_pct > 85          # 교차검증·검증 다수 → 의사결정 가능
    assert t.grade == "의사결정 가능"


def test_trust_determinism():
    a = assess_trust(_cand(), trend=None, policy_is_default=True)
    b = assess_trust(_cand(), trend=None, policy_is_default=True)
    assert a.score_pct == b.score_pct   # 결정론(G3)


def test_trust_reaches_decision_grade_when_fully_verified():
    """모든 입력이 검증되면 ≥95% '의사결정 가능' 도달 (호가 교차검증 잔여 제외 상한 ~97%)."""
    series = [{"deal_ym": f"2025-{m:02d}", "price_krw": p} for m, p in
              [(1, 800_000_000), (3, 805_000_000), (5, 812_000_000), (7, 820_000_000),
               (9, 828_000_000), (11, 835_000_000), (12, 840_000_000), (12, 842_000_000)]]
    tr = compute_trend(series)
    t = assess_trust(_cand(price=840_000_000, land_estimate=False), trend=tr,
                     policy_is_default=False, has_location_signal=True, redev_verified=True)
    assert t.score_pct >= 95.0
    assert t.grade == "의사결정 가능"
    # 잔여는 호가(교차검증)뿐 — 호가는 매물이라 직접 인증 불가, 환원불가능한 상한
    assert all("호가" in b for b in t.blocking) or t.blocking == ()
