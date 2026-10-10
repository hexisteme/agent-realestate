"""Real renderers preserve every observation when the layout changes at narrow widths."""
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from blog import macro_context as mc
from blog.housing_support import render_housing_support_page


def context():
    snap = json.loads((Path(__file__).parent / "fixtures/macro/snapshot_2026-09-07.json").read_text())
    ctx = mc.build_macro_context(snap, "2026-09-07")
    ctx["cards"][0].update(source="한국은행 공식 통계 원문 출처와 관측 기준 설명",
                           next_release_txt="2026-10-22 금통위 발표 예정 · 일정 변경 시 공식 공지를 확인하세요")
    return ctx


def support_snapshot():
    return {"target_date": "2026-10-03", "collected_at": "2026-10-03T11:00:00+09:00", "notices": [{
        "issuer": "sh", "title": "2026년 하반기 신혼·신생아 매입임대주택Ⅰ 입주자 모집공고",
        "url": "https://www.i-sh.co.kr/app/lay2/program/S48T1581C563/www/brd/m_247/view.do?seq=310650&multi_itm_seq=2",
        "published_on": "2026-09-30", "apply_start": "2026-10-13", "apply_end": "2026-10-15",
        "apply_start_at": "2026-10-13T10:00:00+09:00", "apply_end_at": "2026-10-15T17:00:00+09:00",
        "scope": "서울", "eligibility_brief": "무주택세대구성원, 혼인 7년 이내 또는 예비신혼 등. Ⅰ·Ⅱ 중복 신청 불가.",
    }]}


@pytest.mark.parametrize("surface", ["site", "page"])
def test_narrow_cards_retain_all_observation_fields(surface):
    ctx = context()
    body = mc.render_macro_strip_site(ctx) if surface == "site" else mc.render_macro_page(ctx, "2026-09-07")
    soup = BeautifulSoup(body, "html.parser")
    cards = soup.select(".market-mobile-cards article")
    assert cards, "Narrow readers need complete cards instead of squeezed multi-column tables"
    first = cards[0].get_text(" ", strip=True)
    for value in ("한국은행 기준금리", "3.00%", "2026-08-27", "+0.25%p", "직전 2.75%",
                  "2026-10-22 금통위 발표 예정", "한국은행 공식 통계 원문", "1년 전 대비", "같은 값 시작", "연속"):
        assert value in first
    assert cards[0].find("a")["href"].startswith("https://")


def test_graph_input_excludes_observations_rejected_by_card_freshness():
    snap = json.loads((Path(__file__).parent / "fixtures/macro/snapshot_2026-09-07.json").read_text())
    snap["indicators"]["us10y"]["date"] = "2026-08-01"
    ctx = mc.build_macro_context(snap, "2026-09-07")
    assert "visual_snapshot" in ctx
    assert "us10y" not in ctx["visual_snapshot"]["indicators"]
    assert ctx["visual_snapshot"]["indicators"]["bok_base"]["series"] == snap["indicators"]["bok_base"]["series"]


def test_trend_graphs_live_only_on_dedicated_macro_page():
    ctx = context()
    soup = BeautifulSoup(mc.render_macro_page(ctx, "2026-09-07"), "html.parser")
    chart = soup.select_one('[data-market-visuals="macro-trends"]')
    assert chart is not None
    assert chart.find("svg") is not None
    assert "관측값·기준일 전체 보기" in chart.get_text()
    assert 'data-market-visuals="macro-trends"' not in mc.render_macro_strip_site(ctx)
    assert set(ctx["visual_snapshot"]["indicators"]) <= set(mc.TISTORY_STRIP_CODES) | set(mc.HOUSING_CODES) | {"usdkrw"}


def test_support_groups_guidance_separately_from_actual_notices():
    soup = BeautifulSoup(render_housing_support_page(None, today="2026-10-03"), "html.parser")
    guidance = soup.find("section", attrs={"aria-label": "유형별 확인 항목"})
    assert guidance is not None
    assert len(guidance.find_all("article")) >= 3
    assert "공식 안내 확인" in guidance.get_text()
    notices = soup.find("section", attrs={"aria-label": "실제 모집공고"})
    assert notices is not None and not notices.find_all("article")


@pytest.fixture(scope="module")
def local_browser(required_browser):
    # Isolated renderer only; never use an operational browser profile or the network.
    return required_browser


@pytest.mark.parametrize("width", [320, 375, 768, 1024, 1440])
@pytest.mark.parametrize("surface", ["site", "page", "support"])
def test_real_layout_keeps_korean_labels_readable_without_document_overflow(local_browser, width, surface):
    ctx = context()
    if surface == "site":
        # Reproduce host theme pressure independently of the current daily page CSS.
        body = '<style>body{margin:0}.wrap{max-width:720px;margin:auto;padding:20px}td{white-space:nowrap;text-align:right}table{width:100%;border-collapse:collapse}*{box-sizing:border-box}</style><div class="wrap">' + mc.render_macro_strip_site(ctx) + '</div>'
    elif surface == "page":
        body = mc.render_macro_page(ctx, "2026-09-07")
    else:
        body = render_housing_support_page(support_snapshot(), today="2026-10-03")
    page = local_browser.new_page(viewport={"width": width, "height": 900})
    try:
        page.route("**/*", lambda route: route.abort())
        page.set_content(body)
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), surface
        if surface == "support":
            cards = page.locator(".re-support-grid article")
            assert cards.count() >= 3
            a, b = cards.nth(0).bounding_box(), cards.nth(1).bounding_box()
            assert a["width"] >= 240
            if width >= 1024:
                assert abs(a["y"] - b["y"]) < 2
            else:
                assert b["y"] > a["y"]
            assert page.get_by_text("공고별 연령·혼인기간", exact=False).is_visible()
            assert page.get_by_text("Ⅰ·Ⅱ 중복 신청 불가", exact=False).is_visible()
            assert page.get_by_text("10/13 10:00 ~ 10/15 17:00", exact=False).is_visible()
        elif width <= 800:
            assert page.locator(".market-desktop-table").count() > 0
            assert not page.locator(".market-desktop-table").first.is_visible()
            assert page.locator(".market-mobile-cards").first.is_visible()
            card = page.locator(".market-mobile-cards article").first
            assert card.locator("h3").bounding_box()["width"] >= 240
            assert "한국은행 공식 통계 원문" in card.inner_text()
        else:
            assert not page.locator(".market-mobile-cards").first.is_visible()
            label = page.locator(".market-desktop-table tbody tr").first.locator("td").first
            assert label.bounding_box()["width"] >= 180
            assert label.bounding_box()["height"] < 180
            table = page.locator(".market-desktop-table").first
            wrapper = table.locator("xpath=..")
            assert wrapper.evaluate("e=>getComputedStyle(e).overflowX") in ("auto", "scroll")
    finally:
        page.close()
