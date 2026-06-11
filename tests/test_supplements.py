"""R2/R3/R4/R7/R11/R12 보강 테스트."""
from datetime import date

import pytest

from agent_realestate.analysts.finance import compute_comprehensive_tax
from agent_realestate.analysts.trend import compute_trend
from agent_realestate.collectors.lawd import lawd_for_district
from agent_realestate.collectors.naver_live import (build_candidates_from_text,
                                                    collapse_brokers, _candidate_from_dict)
from agent_realestate.domain import (Candidate, DataSource, Listing, PriceKind, RedevStage)


def _listing(price=800_000_000):
    return Listing(complex_name="X", dong_ho="1동 1호", area_exclusive_m2=59, floor="5/15층",
                   facing="남향", price_krw=price, price_kind=PriceKind.ASKING_LIVE,
                   agent_name="공인", confirmed_date=date(2026, 5, 27), source=DataSource.NAVER_LIVE_CHROME)


def _cand(**kw):
    base = dict(listing=_listing(), units=1000, built_year=1990, far_pct=200, land_share_pyeong=10,
                land_share_is_estimate=True, redev_stage=RedevStage.NONE, jeonse_krw=400_000_000,
                transit="역세권", district="X구")
    base.update(kw)
    return Candidate(**base)


# R4 입력 sanity
def test_r4_jeonse_over_price_rejected():
    with pytest.raises(ValueError):
        _cand(jeonse_krw=900_000_000)        # 전세 > 매매(8억)
def test_r4_far_out_of_range_rejected():
    with pytest.raises(ValueError):
        _cand(far_pct=3000)
def test_r4_builtyear_rejected():
    with pytest.raises(ValueError):
        _cand(built_year=1850)
def test_r4_valid_ok():
    assert _cand().jeonse_krw == 400_000_000


# 표본/구간 게이트 (council 20260529: n<5 외삽은 의사결정 무효 → '약')
def test_r7_two_points_unreliable():
    t = compute_trend([{"deal_ym": "2026-01", "price_krw": 800_000_000},
                       {"deal_ym": "2026-05", "price_krw": 830_000_000}])
    assert t.reliable is False        # n<3 (회귀 불가)
    assert t.strength == "약"
def test_r7_three_modest_now_weak():
    # 종전엔 n>=3 이면 reliable=True 였으나 council 수렴으로 n<5 는 '약'(외삽 무효).
    t = compute_trend([{"deal_ym": "2025-05", "price_krw": 800_000_000},
                       {"deal_ym": "2025-11", "price_krw": 810_000_000},
                       {"deal_ym": "2026-05", "price_krw": 840_000_000}])
    assert t.strength == "약"
    assert t.reliable is False
    assert t.band is None             # 약함 → 시나리오 밴드 제공 안 함 (보수 중립으로 대체됨)
def test_trend_five_points_reliable_with_band():
    # n>=5, 완만·일관 → '중' 이상, 회귀 밴드(lo<mid<hi) 제공
    t = compute_trend([{"deal_ym": "2025-01", "price_krw": 800_000_000},
                       {"deal_ym": "2025-04", "price_krw": 808_000_000},
                       {"deal_ym": "2025-07", "price_krw": 818_000_000},
                       {"deal_ym": "2025-10", "price_krw": 828_000_000},
                       {"deal_ym": "2026-01", "price_krw": 840_000_000}])
    assert t.reliable is True
    assert t.strength in ("중", "강")
    b = t.band
    assert b is not None and b[0] < b[1] < b[2]   # lo < mid < hi
    assert t.loo_growth_pct is not None           # leave-one-out 산출됨
def test_r7_huge_growth_unreliable():
    t = compute_trend([{"deal_ym": "2026-01", "price_krw": 600_000_000},
                       {"deal_ym": "2026-02", "price_krw": 700_000_000},
                       {"deal_ym": "2026-03", "price_krw": 800_000_000},
                       {"deal_ym": "2026-04", "price_krw": 900_000_000},
                       {"deal_ym": "2026-05", "price_krw": 1_000_000_000}])
    assert t.strength == "약"          # 과열(>20%/년)
    assert t.band is None


# R3 다중 중개사
def test_r3_collapse_brokers():
    listings = [
        {"dong_ho": "7동", "area_exclusive_m2": 60.0, "price_krw": 920_000_000, "floor": "고/12층", "facing": "남동향", "agent_name": "A공인", "confirmed_date": "2026-05-27"},
        {"dong_ho": "7동", "area_exclusive_m2": 60.0, "price_krw": 920_000_000, "floor": "고/12층", "facing": "남동향", "agent_name": "B공인", "confirmed_date": "2026-05-27"},
    ]
    g = collapse_brokers(listings)
    assert len(g) == 1 and g[0]["broker_count"] == 2


# R2 파서→candidates
SAMPLE = """집주인청량리신현대 7동
매매9억 2,000
아파트82/60m², 고/12층, 남동향
좋은집부동산플러스공인중개사
확인매물 26.05.27."""

def test_r2_build_candidates_from_text():
    enrich = {"청량리신현대": {"units": 736, "built_year": 1989, "far_pct": 204,
                              "land_share_pyeong": 9.5, "land_share_is_estimate": True,
                              "redev_stage": "NONE", "jeonse_krw": 450_000_000,
                              "transit": "회기역 GTX-C", "district": "동대문구"}}
    cands = build_candidates_from_text(SAMPLE, enrich, "청량리신현대")
    assert len(cands) == 1
    assert cands[0]["units"] == 736 and cands[0]["price_krw"] == 920_000_000
    c = _candidate_from_dict(cands[0])      # 도메인 검증 통과
    assert c.listing.area_exclusive_m2 == 60.0 and c.broker_count == 1


# R11 bracket 데이터화 (값 보존)
def test_r11_jongbu_brackets_value():
    assert compute_comprehensive_tax(1_000_000_000, 1) == 0      # ≤12억
    assert compute_comprehensive_tax(2_500_000_000, 1) > 0       # 누진 발생


# R12 LAWD 매핑
def test_r12_lawd():
    assert lawd_for_district("노원구") == "11350"
    assert lawd_for_district("노원") == "11350"
    assert lawd_for_district("서울특별시 강남구 역삼동") == "11680"
    assert lawd_for_district("부산") is None
