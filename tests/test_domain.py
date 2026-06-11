"""G1 타입 가드레일 테스트."""
from datetime import date

import pytest

from agent_realestate.domain import (Claim, DataSource, Listing, PolicyFact,
                                     PriceKind, Provenance)


def _live_listing(**kw):
    base = dict(complex_name="상계주공3", dong_ho="324동 506호", area_exclusive_m2=59.0,
                floor="5/15층", facing="동향", price_krw=830_000_000,
                price_kind=PriceKind.ASKING_LIVE, agent_name="노원공인",
                confirmed_date=date(2026, 5, 27), source=DataSource.NAVER_LIVE_CHROME)
    base.update(kw)
    return Listing(**base)


def test_listing_ok():
    l = _live_listing()
    assert l.pyeong == round(59.0 / 3.305785, 1)


def test_listing_missing_4element_rejected():
    with pytest.raises(ValueError):
        _live_listing(agent_name="")        # 중개사 누락
    with pytest.raises(ValueError):
        _live_listing(dong_ho="")


def test_asking_live_requires_naver_source():
    # 추정/웹검색 출처로 ASKING_LIVE 승격 시도 → 거부 (RDU-061)
    with pytest.raises(ValueError):
        _live_listing(source=DataSource.WEB_SEARCH)


def test_transaction_price_allows_molit():
    l = _live_listing(price_kind=PriceKind.TRANSACTION_REAL, source=DataSource.MOLIT_API)
    assert l.price_kind is PriceKind.TRANSACTION_REAL


def test_fact_claim_needs_evidence():
    with pytest.raises(ValueError):
        Claim("매매 8.3억", Provenance.FACT)          # 근거 없음
    Claim("매매 8.3억", Provenance.FACT, ("naver:1",))  # OK
    Claim("저평가로 보임", Provenance.INFERENCE)          # 추론은 근거 없어도 OK


def test_policyfact_needs_url():
    with pytest.raises(ValueError):
        PolicyFact("생애최초 LTV 70%", url="", confirmed_date=date(2026, 5, 27))


def test_staleness():
    l = _live_listing(confirmed_date=date(2026, 5, 1))
    assert l.is_stale(date(2026, 5, 27), days=14) is True
