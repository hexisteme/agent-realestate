"""decision_prior (v8 의사결정 아키텍처) 결정론·경계 테스트."""
from datetime import date

from agent_realestate.analysts.decision_prior import (catalyst_flags,
                                                      decision_summary,
                                                      district_prior,
                                                      employment_corridor)
from agent_realestate.domain import (Candidate, DataSource, Listing, PriceKind,
                                     RedevStage)


def _cand(cbd=None, transit="", infra=None, stage="NONE", gu_cagr=None):
    lst = Listing(complex_name="가락삼익", dong_ho="101동 101호", area_exclusive_m2=84.0,
                  floor="5/15층", facing="남향", price_krw=900_000_000,
                  price_kind=PriceKind.ASKING_LIVE, agent_name="중개", confirmed_date=date(2026, 6, 4),
                  source=DataSource.NAVER_LIVE_CHROME)
    return Candidate(listing=lst, units=1000, built_year=1990, far_pct=200.0,
                     land_share_pyeong=11.0, land_share_is_estimate=True,
                     redev_stage=RedevStage[stage], jeonse_krw=None, transit=transit,
                     district="서울 송파구", cbd_km=cbd, infra=infra, gu_cagr=gu_cagr)


def test_employment_corridor_ordinal():
    assert employment_corridor(_cand(cbd=3.0)) == 2
    assert employment_corridor(_cand(cbd=7.0)) == 1
    assert employment_corridor(_cand(cbd=12.0)) == 0
    assert employment_corridor(_cand(cbd=None)) is None


def test_catalysts_binary_and_strong():
    # 고용근접(cbd≤8) + 교통(GTX) + 재건축조기(추진위 lvl2) = 3/3 강함
    cat = catalyst_flags(_cand(cbd=5.0, transit="GTX-A 역세권", stage="PROMOTION"))
    assert cat["flags"]["고용근접"] and cat["flags"]["교통"] and cat["flags"]["재건축조기"]
    assert cat["yes"] == 3 and cat["strong"] is True
    assert cat["supply"] is None                     # 공급 미수집=정직 None
    # 0 촉매
    cat0 = catalyst_flags(_cand(cbd=20.0, transit="", stage="NONE"))
    assert cat0["yes"] == 0 and cat0["strong"] is False


def test_redev_early_only_promotion_to_project():
    assert catalyst_flags(_cand(stage="UNION_SETUP"))["flags"]["재건축조기"] is True   # lvl3
    assert catalyst_flags(_cand(stage="MGMT_DISPOSAL"))["flags"]["재건축조기"] is False  # lvl5(후기)
    assert catalyst_flags(_cand(stage="NONE"))["flags"]["재건축조기"] is False


def test_district_prior_range_and_blend():
    hi = district_prior(_cand(cbd=2.0, gu_cagr=10.0))   # 고base+고용강
    lo = district_prior(_cand(cbd=15.0, gu_cagr=6.0))   # 저base+고용약
    assert 2.0 <= lo <= hi <= 5.0 and hi > lo


def test_prior_is_deterministic():
    c = _cand(cbd=5.0, gu_cagr=8.0)
    assert district_prior(c) == district_prior(c)
    s = decision_summary(c)
    assert s["prior"] == district_prior(c) and s["supply_known"] is False


def test_liquidity_tailwind_regime_conditional():
    from agent_realestate.analysts.decision_prior import liquidity_tailwind
    cs = [_cand() for _ in range(5)]
    # trade_annual 주입(frozen dataclass라 새로 생성)
    import dataclasses
    cs = [dataclasses.replace(c, trade_annual=float(v)) for c, v in zip(cs, [5, 20, 40, 80, 100])]
    hi = cs[-1]   # 최고 거래량(상위)
    # 활성 국면: 고거래량 → tailwind True
    assert liquidity_tailwind(hi, cs, regime_active=True)["tailwind"] is True
    # 비활성(과열): 표시는 '상'이나 tailwind False (정직)
    r = liquidity_tailwind(hi, cs, regime_active=False)
    assert r["level"] == "상" and r["tailwind"] is False
    # 저거래량은 활성국면이어도 tailwind 아님
    assert liquidity_tailwind(cs[0], cs, regime_active=True)["tailwind"] is False
