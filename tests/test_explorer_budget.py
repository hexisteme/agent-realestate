"""Real Chromium regressions for budget presets, copied links, and analytics privacy.

The page and dataset are fulfilled at a synthetic HTTPS origin; all other requests
are aborted. Mutation apertures alter only an in-memory template, never production.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit

import pytest

from blog.build_explorer import EXPLORER_HTML


FIXTURE_ORIGIN = "https://explorer-fixture.test"
PRIVATE_QUERY = "합성비공개검색표식"
BUDGET_ROWS = (
    ("10억 미만", ((PRIVATE_QUERY + "단지", 5), ("10억직전단지", 9.99))),
    ("10~15억", (("10억경계단지", 10), ("15억직전단지", 14.99))),
    ("15~20억", (("15억경계단지", 15), ("20억직전단지", 19.99))),
    ("20억 이상", (("20억경계단지", 20), ("25억단지", 25))),
)


def _budget_dataset():
    # Explicit independent labels: these tests exercise the URL/UI contract, not
    # recompute price_segment with the production function under test.
    rows = []
    for segment, entries in BUDGET_ROWS:
        for name, price in entries:
            rows.append({"name": name, "gu": "강남구", "saeng": "역삼동",
                         "area_m2": 59, "pyeong": 17.8, "area_band": "~59㎡",
                         "units": 500, "built_year": 1990, "decade": "1990년대",
                         "product_type": "아파트", "molit_recent_eok": price,
                         "molit_n": 10, "price_segment": segment})
    rows.append({**rows[0], "name": "실거래없는단지", "molit_recent_eok": None,
                 "molit_n": 0, "price_segment": None})
    return {"complexes": rows, "count": len(rows), "data_asof": "2026-09-26",
            "generated": "2026-09-26", "license": "Synthetic test fixture",
            "disclaimer": "합성 검증 데이터 — 실매물이 아님", "takedown": "",
            "sources": []}


@pytest.fixture(scope="module")
def budget_evidence():
    evidence = {"schema": "ExplorerBudgetBrowserEvidence/v1",
                "source": "blog.build_explorer.EXPLORER_HTML",
                "input": "synthetic dataset with explicit four budget labels and one unpriced row",
                "network": "fixture HTML/dataset fulfilled; all other requests aborted",
                "viewport": {"width": 390, "height": 844}, "observations": []}
    yield evidence
    output = os.environ.get("EXPLORER_BUDGET_EVIDENCE_DIR")
    if output:
        out = Path(output)
        out.mkdir(parents=True, exist_ok=True)
        (out / "explorer-budget-result.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def budget_browser(budget_evidence):
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        budget_evidence["browser"] = "Chromium " + browser.version
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def open_budget_page(budget_browser):
    contexts = []

    def open_page(query, template=EXPLORER_HTML):
        context = budget_browser.new_context(
            viewport={"width": 390, "height": 844},
            permissions=["clipboard-read", "clipboard-write"])
        contexts.append(context)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("window.__capturedGtag=[];window.gtag=(...args)=>window.__capturedGtag.push(args);")
        dataset = json.dumps(_budget_dataset(), ensure_ascii=False)

        def fulfill_fixture(route):
            url = urlsplit(route.request.url)
            if url.scheme == "https" and url.netloc == "explorer-fixture.test":
                if url.path == "/explorer.html":
                    route.fulfill(status=200, content_type="text/html; charset=utf-8", body=template)
                    return
                if url.path == "/dataset.json":
                    route.fulfill(status=200, content_type="application/json; charset=utf-8", body=dataset)
                    return
            route.abort()

        page.route("**/*", fulfill_fixture)
        page.goto(FIXTURE_ORIGIN + "/explorer.html?" + urlencode(query), wait_until="domcontentloaded")
        _wait_for_rows(page)
        assert errors == []
        return page

    yield open_page
    for context in contexts:
        context.close()


def _wait_for_rows(page):
    page.wait_for_function("document.getElementById('count').textContent.includes('개 매물')")


def _shown_names(page):
    return sorted(page.locator("#tbody tr td:first-child").all_text_contents())


def _copy_state_url(page):
    page.get_by_role("button", name="링크 복사", exact=True).click()
    page.wait_for_function("document.getElementById('copyLinkBtn').textContent==='복사됨'")
    # Read the real browser clipboard, not a replacement implementation of copy.
    return page.evaluate("navigator.clipboard.readText()")


def _preset_payload(page):
    events = page.evaluate("window.__capturedGtag")
    presets = [event[2] for event in events if event[:2] == ["event", "explorer_preset"]]
    assert len(presets) == 1
    return presets[0]


def _assert_budget_rows(page, entries):
    assert _shown_names(page) == sorted(name for name, _ in entries)
    assert page.locator("#count").inner_text() == "2개 매물"


def _assert_no_raw_search(payload):
    assert set(payload) == {"gu", "band", "seg", "has_search"}
    assert payload["has_search"] is True
    serialized = json.dumps(payload, ensure_ascii=False)
    assert PRIVATE_QUERY not in serialized
    assert quote(PRIVATE_QUERY, safe="") not in serialized


@pytest.mark.parametrize(("segment", "entries"), BUDGET_ROWS)
def test_budget_preset_filters_visible_rows_and_roundtrips_the_copied_url(
        open_budget_page, budget_evidence, segment, entries):
    page = open_budget_page({"seg": segment})
    _assert_budget_rows(page, entries)
    assert page.locator('.chip[data-k="seg"].on').all_text_contents() == [segment]
    payload = _preset_payload(page)
    assert payload["seg"] == segment and payload["has_search"] is False
    copied = _copy_state_url(page)
    query = parse_qs(urlsplit(copied).query)
    assert urlsplit(copied).netloc == "explorer-fixture.test"
    assert query == {"seg": [segment], "sort": ["molit_recent_eok"], "dir": ["-1"]}
    page.goto(copied, wait_until="domcontentloaded")
    _wait_for_rows(page)
    _assert_budget_rows(page, entries)
    assert page.locator('.chip[data-k="seg"].on').all_text_contents() == [segment]
    layout = page.evaluate("({viewport:innerWidth,document:document.documentElement.scrollWidth})")
    output = os.environ.get("EXPLORER_BUDGET_EVIDENCE_DIR")
    screenshot = None
    if output:
        out = Path(output)
        out.mkdir(parents=True, exist_ok=True)
        screenshot = f"explorer-budget-{[item[0] for item in BUDGET_ROWS].index(segment)}-390.png"
        page.locator("#count").scroll_into_view_if_needed()
        page.screenshot(path=str(out / screenshot))
    budget_evidence["observations"].append({
        "kind": "production_behavior", "check": "budget_filter_and_clipboard_roundtrip",
        "segment": segment, "shown_names": _shown_names(page), "shown_count": len(entries),
        "copied_url": copied, "preset_event": payload, "mobile_width": layout, "screenshot": screenshot})


def test_unknown_budget_is_ignored_by_filter_sharing_and_analytics(open_budget_page, budget_evidence):
    page = open_budget_page({"seg": "not-a-budget"})
    expected = sorted(row["name"] for row in _budget_dataset()["complexes"])
    assert _shown_names(page) == expected
    assert page.locator('.chip[data-k="seg"].on').count() == 0
    copied = _copy_state_url(page)
    assert "seg" not in parse_qs(urlsplit(copied).query)
    payload = _preset_payload(page)
    assert payload["seg"] == ""
    budget_evidence["observations"].append({
        "kind": "production_behavior", "check": "unknown_budget_ignored",
        "shown_count": len(expected), "copied_url": copied, "preset_event": payload})


def test_preset_search_is_applied_but_not_sent_in_analytics(open_budget_page, budget_evidence):
    page = open_budget_page({"seg": "10억 미만", "q": PRIVATE_QUERY})
    assert _shown_names(page) == [PRIVATE_QUERY + "단지"]
    assert page.locator("#q").input_value() == PRIVATE_QUERY
    payload = _preset_payload(page)
    _assert_no_raw_search(payload)
    assert payload["seg"] == "10억 미만"
    budget_evidence["observations"].append({
        "kind": "production_behavior", "check": "search_applied_without_raw_query_analytics",
        "shown_count": 1, "search_input_applied": True, "preset_event": payload,
        "raw_query_present": False})


@pytest.mark.parametrize("segment", tuple(item[0] for item in BUDGET_ROWS))
def test_budget_mobile_document_fits_viewport_and_table_keeps_local_scroll(
        open_budget_page, budget_evidence, segment):
    page = open_budget_page({"seg": segment})
    layout = page.evaluate("""() => {
      const table=document.getElementById('tbl'), frame=table.parentElement;
      return {viewport:innerWidth,document:document.documentElement.scrollWidth,
              frame_width:frame.clientWidth,table_scroll_width:frame.scrollWidth,
              table_overflow:getComputedStyle(frame).overflowX};
    }""")
    budget_evidence["observations"].append({
        "kind": "production_behavior", "check": "mobile_document_and_local_table_scroll",
        "segment": segment, "layout": layout})
    assert layout["document"] <= layout["viewport"] == 390
    assert layout["table_overflow"] == "auto"
    assert layout["table_scroll_width"] > layout["frame_width"]


def _legacy_mutation_aperture(kind):
    if kind == "missing_budget_filter":
        before = 'const seg=p.get("seg"); if(seg&&SEG_ORDER.includes(seg)) S.seg.add(seg);'
        after = 'const seg=p.get("seg");'
    elif kind == "missing_shared_budget":
        before = 'if(S.seg.size) p.set("seg",[...S.seg][0]);'
        after = ""
    else:
        before = "track('explorer_preset',{gu:gu||'',band:band||'',seg:S.seg.size?seg:'',has_search:!!q});"
        after = "track('explorer_preset',{params:qs.slice(0,200)});"
    assert EXPLORER_HTML.count(before) == 1
    return EXPLORER_HTML.replace(before, after, 1)


@pytest.mark.parametrize("mutation", ("missing_budget_filter", "missing_shared_budget", "raw_query_analytics"))
def test_isolated_legacy_mutations_are_rejected_by_browser_assertions(
        open_budget_page, budget_evidence, mutation):
    query = {"seg": "10억 미만"}
    if mutation == "raw_query_analytics":
        query["q"] = PRIVATE_QUERY
    page = open_budget_page(query, _legacy_mutation_aperture(mutation))
    if mutation == "missing_budget_filter":
        with pytest.raises(AssertionError):
            _assert_budget_rows(page, BUDGET_ROWS[0][1])
        observed = {"shown_count": len(_shown_names(page))}
    elif mutation == "missing_shared_budget":
        copied = _copy_state_url(page)
        with pytest.raises(AssertionError):
            assert parse_qs(urlsplit(copied).query).get("seg") == ["10억 미만"]
        observed = {"shared_budget_present": "seg" in parse_qs(urlsplit(copied).query)}
    else:
        payload = _preset_payload(page)
        with pytest.raises(AssertionError):
            _assert_no_raw_search(payload)
        assert quote(PRIVATE_QUERY, safe="") in payload["params"]
        observed = {"raw_query_present": True, "event_fields": sorted(payload)}
    budget_evidence["observations"].append({
        "kind": "synthetic_mutation_aperture", "production_modified": False,
        "mutation": mutation, "rejected_by_behavior_assertions": True, "observed": observed})
