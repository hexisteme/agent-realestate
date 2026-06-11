"""주상복합 재건축축 결함 수정 검증 (2026-06-04 사용자 지적 + council/sage 수렴).
주상복합은 재건축 사실상 불가 → 토지지분 중립(2.0) + 재건축잠재 0. age는 재건축 plus 아님."""
from datetime import date

from agent_realestate.analysts.scoring import (_is_jusang, _land_share,
                                               _redev_potential)
from agent_realestate.domain import (Candidate, DataSource, Listing, PriceKind,
                                      RedevStage)


def _cand(name, far, built, units=1000, stage="NONE"):
    lst = Listing(complex_name=name, dong_ho="101동 101호", area_exclusive_m2=84.0,
                  floor="5/15층", facing="남향", price_krw=900_000_000,
                  price_kind=PriceKind.ASKING_LIVE, agent_name="중개", confirmed_date=date(2026, 6, 4),
                  source=DataSource.NAVER_LIVE_CHROME)
    return Candidate(listing=lst, units=units, built_year=built, far_pct=float(far),
                     land_share_pyeong=11.0, land_share_is_estimate=True,
                     redev_stage=RedevStage[stage], jeonse_krw=None, transit="",
                     district="서울 종로구")


def test_is_jusang_detects_suffix():
    assert _is_jusang(_cand("숭인[주상복합]", 158, 1979)) is True
    assert _is_jusang(_cand("상계주공2단지", 171, 1987)) is False


def test_jusang_land_share_neutral_not_max():
    # 숭인: 저용적률(158)이어도 주상복합이라 토지지분 중립 2.0 (아파트라면 5.0이었을 것)
    j = _cand("숭인[주상복합]", 158, 1979)
    apt = _cand("상계주공2단지", 158, 1979)
    assert _land_share(j) == 2.0
    assert _land_share(apt) == 5.0          # 동일 far 아파트는 만점(재건축 토지가치 실현 가능)


def test_jusang_redev_potential_zero():
    # 주상복합 재건축잠재 = 0 (저용적률·초노후여도)
    assert _redev_potential(_cand("숭인[주상복합]", 158, 1979)) == 0.0
    # 동일 조건 아파트는 양(+) 재건축잠재 (저far gap + 노후)
    assert _redev_potential(_cand("상계주공2단지", 158, 1979)) > 2.0


def test_age_credit_gated_on_far_gap():
    # 용적률 여지 없는(고far) 노후 아파트: age 가 재건축 plus 가 아님(gap 게이팅)
    maxed = _cand("고밀노후아파트", 480, 1985)    # gap≈(500-480)/500=0.04<0.1 (역세권 가정 없으면 ceiling300→gap0)
    # ceiling 300, far 480 → gap=0 → age/unit/stage 게이팅으로 재건축잠재 0~경미
    assert _redev_potential(maxed) <= 0.5
    # 용적률 여지 큰 노후 아파트는 (고밀 0.0 대비) 의미있는 재건축잠재 유지.
    # ★2026-06-04 용적률 직교화: far-gap 계수 5.0→2.0(H3 재개발 alpha NULL·토지지분 중복 해소)로 절대크기 하향
    #   (2.5→~2.27). gating 의도(고밀=0 ↔ 저밀=credit)는 보존 — 임계만 재calibrate.
    assert _redev_potential(_cand("저밀노후아파트", 170, 1985)) > 2.0


def test_deterministic():
    c = _cand("숭인[주상복합]", 158, 1979)
    assert _redev_potential(c) == _redev_potential(c) == 0.0
    assert _land_share(c) == _land_share(c) == 2.0
