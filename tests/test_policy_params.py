"""P0-2 정책 외부화 + P0-1 staleness/파서."""
from datetime import date

import agent_realestate.config as cfg
from agent_realestate.analysts.finance import compute_acquisition_tax
from agent_realestate.analysts.risk import assess_flags
from agent_realestate.cache import store
from agent_realestate.collectors.naver_live import parse_eok, parse_naver_listings
from agent_realestate.domain import (Candidate, DataSource, ExitStrategy, Listing,
                                     PriceKind, RedevStage)
from agent_realestate.policy_params import PolicyParams


def test_params_default_is_flagged():
    p = PolicyParams()
    assert p.is_default is True and p.confirmed_date == "default"


def test_params_from_cache_override(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "CACHE_DB", tmp_path / "t.sqlite")
    conn = store.connect()
    store.upsert_param(conn, "official_price_ratio", 0.70, "https://molit", "2026-05-28")
    store.upsert_param(conn, "acq_first_relief", 2000000, "https://law", "2026-05-28")
    p = PolicyParams.from_cache(conn)
    assert p.is_default is False
    assert p.official_price_ratio == 0.70
    assert p.confirmed_date == "2026-05-28"


def test_acq_tax_uses_params():
    # 공시가율 등과 무관하지만 감면 한도를 param 으로 바꾸면 결과 변함
    p_relief0 = PolicyParams(acq_first_relief=0)
    base = compute_acquisition_tax(830_000_000, True, 59, PolicyParams())
    no_relief = compute_acquisition_tax(830_000_000, True, 59, p_relief0)
    assert no_relief - base == 2_000_000


def _stale_listing(d):
    return Candidate(
        listing=Listing(complex_name="X", dong_ho="1동 1호", area_exclusive_m2=59, floor="5/15층",
                        facing="남향", price_krw=800_000_000, price_kind=PriceKind.ASKING_LIVE,
                        agent_name="공인", confirmed_date=d, source=DataSource.NAVER_LIVE_CHROME),
        units=1000, built_year=1990, far_pct=200, land_share_pyeong=10, land_share_is_estimate=True,
        redev_stage=RedevStage.NONE, jeonse_krw=400_000_000, transit="역세권", district="X구")


def test_staleness_flag():
    c = _stale_listing(date(2026, 5, 1))
    flags = assess_flags(c, ExitStrategy.HOLD_AND_RENT, today=date(2026, 5, 28))
    assert any(f.code == "F_STALE" for f in flags)
    # 최신이면 플래그 없음
    fresh = _stale_listing(date(2026, 5, 27))
    assert not any(f.code == "F_STALE" for f in assess_flags(fresh, ExitStrategy.HOLD_AND_RENT, today=date(2026, 5, 28)))


def test_parse_eok():
    assert parse_eok("9억 2,000") == 920_000_000
    assert parse_eok("12억") == 1_200_000_000
    assert parse_eok("8억 5,000") == 850_000_000


SAMPLE = """집주인청량리신현대 7동
매매9억 2,000
아파트82/60m², 고/12층, 남동향
신현대 깔끔한 상태 매매입니다
좋은집부동산플러스공인중개사
확인매물 26.05.27.
집주인청량리신현대 8동
매매9억 8,000
아파트90/72m², 중/15층, 남서향
하늘부동산중개사무소
확인매물 26.05.27."""


def test_parse_naver_listings():
    ls = parse_naver_listings(SAMPLE, "청량리신현대")
    assert len(ls) == 2
    assert ls[0]["dong_ho"] == "7동"
    assert ls[0]["price_krw"] == 920_000_000
    assert ls[0]["area_exclusive_m2"] == 60.0
    assert ls[0]["facing"] == "남동향"
    assert ls[0]["confirmed_date"] == "2026-05-27"
    assert "공인중개사" in ls[0]["agent_name"]
