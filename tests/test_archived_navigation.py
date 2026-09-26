"""Archived anchors follow current pages without restoring pruned price owners."""
from __future__ import annotations

import json
from html.parser import HTMLParser
from urllib.parse import quote

import pytest


BASE = "https://example.test/research"


class _Links(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.links = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.extend(value for name, value in attrs if name == "href")


def _row():
    return dict(gu="노원", name="표본미달단지", saeng="", product_type="아파트", area_m2=59.0,
                pyeong=17.8, units=1000, built_year=1990, decade="1990년대", area_band="~59㎡",
                molit_n=5, molit_recent_eok=6.0, molit_p25_eok=5.5, molit_p75_eok=6.5,
                molit_trend_dir="—", molit_trend_pct=None, molit_pos_52w=None,
                pyeong_price_man=3000, price_segment="10억 미만", jeonse_n=None,
                jeonse_ratio_complex_pct=None, jeonse_recent_eok=None, gap_eok=None,
                trade_annual=None, turnover_pct=None)


@pytest.mark.parametrize("archive", ("posts", "daily"))
def test_actual_build_repairs_archived_href_after_prune_without_lowering_page_gate(tmp_path, monkeypatch, archive):
    from blog import build_site

    source, site = tmp_path / "source", tmp_path / "site"
    (source / "posts").mkdir(parents=True)
    (source / "daily").mkdir()
    row = _row()
    dataset = {"complexes": [row], "count": 1, "data_asof": "2026-09-26", "generated": "2026-09-26"}
    (source / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    href = f"../complex/{quote('노원-표본미달단지')}.html?utm_campaign=archive&utm_content=slot"
    original = f'<html><body><p>2026-09-05 과거 관측 8.5억</p><a href="{href}">단지 확인</a></body></html>'
    (source / archive / "2026-09-05.html").write_text(original, encoding="utf-8")
    (site / "complex").mkdir(parents=True)
    (site / "complex" / "노원-표본미달단지.html").write_text("<p>옛 단지 본문</p>", encoding="utf-8")
    monkeypatch.setattr(build_site, "SITE", str(site))
    monkeypatch.setattr(build_site, "SRC", str(source))
    monkeypatch.setattr(build_site, "BASE_URL", BASE)
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "")
    monkeypatch.delenv("BLOG_ACQUISITION_PROBE", raising=False)
    monkeypatch.delenv("BLOG_ACQUISITION_COHORT", raising=False)

    receipt = build_site.build("2026-09-26", molit_path=str(tmp_path / "absent-molit.json"))

    actual = (site / archive / "2026-09-05.html").read_text(encoding="utf-8")
    assert _Links(actual).links == [f"../gu/{quote('노원')}.html?utm_campaign=archive&utm_content=slot#{quote('표본미달단지')}"]
    assert "2026-09-05 과거 관측 8.5억" in actual
    assert not list((site / "complex").glob("*.html"))
    assert receipt["complex"] == 0
    assert receipt["archived_navigation"]["changed_links"] == 1


def _archive_site(tmp_path, source, gu_html='<table><tr id="단지"><td>현재 표</td></tr></table>'):
    site = tmp_path / "site"
    (site / "posts").mkdir(parents=True)
    (site / "daily").mkdir()
    (site / "gu").mkdir()
    (site / "complex").mkdir()
    archive = site / "posts" / "2026-09-05.html"
    archive.write_bytes(source.encode("utf-8"))
    (site / "gu" / "노원.html").write_text(gu_html, encoding="utf-8")
    return site, archive


def _repair(site):
    from blog.archive_navigation import repair_archived_navigation
    return repair_archived_navigation(site, BASE)


@pytest.mark.parametrize("wrapper", ('"{}"', "'{}'", "{}"))
def test_only_actual_href_attribute_changes_with_utf8_crlf_and_script_examples(tmp_path, wrapper):
    old = "../complex/노원-단지.html?utm_source=옛글&amp;utm_content=a%26b"
    actual_tag = f'<A title="비교 > 기준" data-href="{old}" HREF={wrapper.format(old)}>과거 링크</A>'
    context = f'<p>2026-09-05 가격 8.5억</p>\r\n<script>const sample = \'<a href="{old}">예시</a>\';</script>\r\n<!-- <a href="{old}">주석</a> -->'
    source = context + "\r\n" + actual_tag
    site, archive = _archive_site(tmp_path, source)
    receipt = _repair(site)
    new = f"../gu/{quote('노원')}.html?utm_source=옛글&amp;utm_content=a%26b#{quote('단지')}"
    wrapped = wrapper.format(new) if wrapper != "{}" else f'"{new}"'
    expected = context + "\r\n" + f'<A title="비교 > 기준" data-href="{old}" HREF={wrapped}>과거 링크</A>'
    assert archive.read_bytes() == expected.encode("utf-8")
    assert receipt["changed_files"] == receipt["changed_links"] == 1
    assert receipt["unresolved"] == []
    assert _repair(site)["changed_links"] == 0


@pytest.mark.parametrize("href,want", (
    ("../complex/노원-단지.html", f"../gu/{quote('노원')}.html#{quote('단지')}"),
    (f"/research/complex/{quote('노원-단지')}.html", f"/research/gu/{quote('노원')}.html#{quote('단지')}"),
    (f"{BASE}/complex/{quote('노원-단지')}.html?utm_campaign=d0", f"{BASE}/gu/{quote('노원')}.html?utm_campaign=d0#{quote('단지')}"),
    ("//example.test/research/complex/노원-단지.html", f"//example.test/research/gu/{quote('노원')}.html#{quote('단지')}"),
    ("../complex/노원-단지.html?#old", f"../gu/{quote('노원')}.html?#{quote('단지')}"),
))
def test_local_url_forms_preserve_origin_base_prefix_and_query(tmp_path, href, want):
    site, archive = _archive_site(tmp_path, f'<a href="{href}">archive</a>')
    receipt = _repair(site)
    assert _Links(archive.read_text()).links == [want]
    assert receipt["changes"][0]["href"] == href
    assert receipt["changes"][0]["new_href"] == want


@pytest.mark.parametrize("href", (
    "https://elsewhere.test/research/complex/노원-단지.html",
    "https://example.test/other/complex/노원-단지.html",
    "https://example.test/research-other/complex/노원-단지.html",
    "/complex/노원-단지.html", "/research-other/complex/노원-단지.html",
    "http://example.test/research/complex/노원-단지.html",
    "mailto:person@example.test", "javascript:void(0)",
))
def test_external_urls_and_other_base_prefixes_are_byte_identical(tmp_path, href):
    source = f'<a href="{href}">original</a>'
    site, archive = _archive_site(tmp_path, source)
    receipt = _repair(site)
    assert archive.read_bytes() == source.encode()
    assert receipt["changed_links"] == 0


def test_missing_fragment_falls_back_but_valid_fragment_and_existing_complex_stay_exact(tmp_path):
    valid = f'../gu/{quote("노원")}.html#{quote("단지")}'
    missing = f'../gu/{quote("노원")}.html?utm_source=archive&amp;utm_content=x#{quote("사라짐")}'
    preserved = '../complex/노원-존재.html?utm_source=archive'
    source = f'<a href="{valid}">valid</a><a href="{missing}">gone</a><a href="{preserved}">existing</a>'
    site, archive = _archive_site(tmp_path, source)
    (site / "complex" / "노원-존재.html").write_text("<p>existing</p>")
    receipt = _repair(site)
    assert archive.read_text() == source.replace(f'{missing}"', f'../gu/{quote("노원")}.html?utm_source=archive&amp;utm_content=x"', 1)
    assert receipt["changed_links"] == 1
    assert receipt["unresolved"] == []


def test_complex_falls_back_to_gu_when_its_id_is_absent_or_only_in_script(tmp_path):
    site, archive = _archive_site(tmp_path, '<a href="../complex/노원-단지.html?x=1">old</a>', '<script>const fake = \'<tr id="단지">\';</script><p id="다른단지">current</p>')
    receipt = _repair(site)
    assert _Links(archive.read_text()).links == [f'../gu/{quote("노원")}.html?x=1']
    assert receipt["changed_links"] == 1


def test_real_id_entities_and_unicode_name_are_not_guessed_or_double_encoded(tmp_path):
    site, archive = _archive_site(tmp_path, '<a href="../complex/노원-빛%26숲.html">old</a>', '<tr id="빛&amp;숲"><td>current</td></tr>')
    _repair(site)
    assert _Links(archive.read_text()).links == [f'../gu/{quote("노원")}.html#{quote("빛&숲")}']


@pytest.mark.parametrize("href,code", (
    ("../complex/없는구-단지.html", "MISSING_CURRENT_GU"),
    ("../posts/없음.html", "MISSING_FILE_OUTSIDE_REPAIR_POLICY"),
    ("../gu/없는구.html#단지", "MISSING_FILE_OUTSIDE_REPAIR_POLICY"),
))
def test_unrepairable_local_links_remain_broken_and_explicitly_unresolved(tmp_path, href, code):
    source = f'<a href="{href}">unchanged</a>'
    site, archive = _archive_site(tmp_path, source)
    receipt = _repair(site)
    assert archive.read_bytes() == source.encode()
    assert receipt["changed_links"] == 0
    assert receipt["unresolved"] == [{"source": "posts/2026-09-05.html", "href": href, "code": code}]
    assert "status" not in receipt  # A narrow repair receipt is never a whole-site PASS.


def test_source_and_target_symlinks_never_escape_archive_scope(tmp_path):
    source = '<a href="../complex/노원-단지.html">original</a>'
    site, archive = _archive_site(tmp_path, source)
    outside = tmp_path / "outside.html"
    outside.write_text(source)
    (site / "posts" / "symlink.html").symlink_to(outside)
    (site / "complex" / "노원-단지.html").symlink_to(outside)
    receipt = _repair(site)
    assert outside.read_text() == source
    assert archive.read_text() == source
    assert receipt["changed_links"] == 0
    assert {entry["code"] for entry in receipt["unresolved"]} == {"ARCHIVE_FILE_OUTSIDE_SCOPE", "INVALID_OR_UNCONFINED_LOCAL_URL"}


def test_encoded_traversal_is_not_a_local_repair_target(tmp_path):
    source = '<a href="../complex/%2e%2e/%2e%2e/outside.html">unchanged</a>'
    site, archive = _archive_site(tmp_path, source)
    receipt = _repair(site)
    assert archive.read_text() == source
    assert receipt["unresolved"][0]["code"] == "INVALID_OR_UNCONFINED_LOCAL_URL"


def test_other_site_directories_and_gu_facts_are_never_rewritten(tmp_path):
    source = '<a href="../complex/노원-단지.html">old</a>'
    site, archive = _archive_site(tmp_path, source)
    other = site / "unowned.html"
    other.write_text(source)
    gu = site / "gu" / "노원.html"
    before = gu.read_bytes()
    _repair(site)
    assert other.read_text() == source
    assert gu.read_bytes() == before
    assert archive.read_text() != source


def test_malformed_percent_fragment_is_reported_without_rewriting_or_crashing(tmp_path):
    source = '<a href="../gu/노원.html#%FF">unchanged</a>'
    site, archive = _archive_site(tmp_path, source)
    receipt = _repair(site)
    assert archive.read_text() == source
    assert receipt["unresolved"][0]["code"] == "INVALID_OR_UNCONFINED_LOCAL_URL"


def test_duplicate_href_is_not_rewritten_as_if_attribute_ownership_were_clear(tmp_path):
    source = '<a href="../complex/노원-단지.html" href="https://elsewhere.test/">unchanged</a>'
    site, archive = _archive_site(tmp_path, source)
    receipt = _repair(site)
    assert archive.read_text() == source
    assert receipt["changed_links"] == 0
    assert receipt["unresolved"][0]["code"] == "AMBIGUOUS_HREF"


def test_actual_build_runs_repair_before_cohort_restore_dispatch(tmp_path, monkeypatch):
    """Ordering fixture, not proof of real D0 manifest authorization or 16-owner hashes."""
    from blog import acquisition_probe, build_site

    source, site = tmp_path / "source", tmp_path / "site"
    (source / "posts").mkdir(parents=True)
    (source / "daily").mkdir()
    frozen = '<html><a href="../complex/노원-표본미달단지.html">frozen D0 text</a></html>'
    archived = source / "posts" / "2026-09-05.html"
    archived.write_text(frozen)
    (source / "dataset.json").write_text(json.dumps({"complexes": [_row()], "count": 1,
                                                   "data_asof": "2026-09-26", "generated": "2026-09-26"}))
    monkeypatch.setattr(build_site, "SITE", str(site))
    monkeypatch.setattr(build_site, "SRC", str(source))
    monkeypatch.setattr(build_site, "BASE_URL", BASE)
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "")
    monkeypatch.delenv("BLOG_ACQUISITION_PROBE", raising=False)
    monkeypatch.delenv("BLOG_ACQUISITION_COHORT", raising=False)

    def frozen_restore_boundary(_cohort, site_dir, _base_url, intents, *, today):
        destination = site / "posts" / "2026-09-05.html"
        assert str(site) == site_dir and today == "2026-09-26"
        assert _Links(destination.read_text()).links == [f'../gu/{quote("노원")}.html#{quote("표본미달단지")}']
        destination.write_bytes(frozen.encode())
        return intents

    monkeypatch.setattr(acquisition_probe, "restore_probe_cohort", frozen_restore_boundary)
    receipt = build_site.build("2026-09-26", molit_path=str(tmp_path / "absent.json"),
                               cohort_path=str(tmp_path / "synthetic-ordering-cohort.json"))
    assert (site / "posts" / archived.name).read_bytes() == frozen.encode()
    assert receipt["archived_navigation"]["changed_links"] == 1


@pytest.mark.parametrize("text_element", ("title", "textarea"))
def test_markup_in_html_text_elements_is_not_an_actual_anchor(tmp_path, text_element):
    example = '<a href="../complex/노원-단지.html">example text</a>'
    source = f'<{text_element}>{example}</{text_element}>' + '<a href="../complex/노원-단지.html">real link</a>'
    site, archive = _archive_site(tmp_path, source)
    receipt = _repair(site)
    expected = f'<{text_element}>{example}</{text_element}>' + f'<a href="../gu/{quote("노원")}.html#{quote("단지")}">real link</a>'
    assert archive.read_text() == expected
    assert receipt["changed_links"] == 1


@pytest.mark.parametrize("text_element", ("title", "textarea"))
def test_markup_in_html_text_elements_does_not_invent_current_gu_ids(tmp_path, text_element):
    site, archive = _archive_site(tmp_path, '<a href="../complex/노원-단지.html">old</a>',
                                  f'<{text_element}><b id="단지">example text</b></{text_element}>')
    _repair(site)
    assert _Links(archive.read_text()).links == [f'../gu/{quote("노원")}.html']


def test_inactive_template_anchor_bytes_are_preserved_while_live_anchor_is_repaired(tmp_path):
    example = '<a href="../complex/노원-단지.html">inactive</a>'
    source = f'<template>{example}<template>{example}</template></template>' + '<a href="../complex/노원-단지.html">live</a>'
    site, archive = _archive_site(tmp_path, source)
    receipt = _repair(site)
    assert archive.read_text() == f'<template>{example}<template>{example}</template></template>' + f'<a href="../gu/{quote("노원")}.html#{quote("단지")}">live</a>'
    assert receipt["changed_links"] == 1


@pytest.mark.parametrize("template_open", ('<template id="real-template">', '<template id="real-template"/>'))
def test_template_content_ids_are_not_document_ids_but_template_own_id_is_preserved(tmp_path, template_open):
    current = template_open + '<template><b id="단지">inactive</b></template></template>'
    source = '<a href="../complex/노원-단지.html">old</a><a href="../gu/노원.html#real-template">valid</a>'
    site, archive = _archive_site(tmp_path, source, current)
    _repair(site)
    assert _Links(archive.read_text()).links == [f'../gu/{quote("노원")}.html', '../gu/노원.html#real-template']


@pytest.mark.parametrize("opening", ('<plaintext id="plain-real">', '<plaintext id="plain-real"/>'))
def test_plaintext_preserves_every_byte_after_opening_including_apparent_closing_tag(tmp_path, opening):
    from blog.archive_navigation import _ElementIds

    old = '../complex/노원-단지.html?utm_content=archive'
    real = f'<a id="before" href="{old}">real</a>'
    text = opening + f'<a id="fake-one" href="{old}">text</a></plaintext><a id="fake-two" href="{old}">still text</a>'
    site, archive = _archive_site(tmp_path, real + text)
    receipt = _repair(site)
    repaired = f'<a id="before" href="../gu/{quote("노원")}.html?utm_content=archive#{quote("단지")}">real</a>'
    assert archive.read_bytes() == (repaired + text).encode("utf-8")
    assert receipt["changed_links"] == 1
    assert _ElementIds(real + text).ids == {"before", "plain-real"}


def test_plaintext_does_not_invent_ids_even_after_an_apparent_closing_tag(tmp_path):
    current = '<plaintext id="real"><b id="단지">text</b></plaintext><b id="단지">still text</b>'
    source = '<a href="../complex/노원-단지.html">old</a><a href="../gu/노원.html#real">valid</a>'
    site, archive = _archive_site(tmp_path, source, current)
    receipt = _repair(site)
    assert _Links(archive.read_text()).links == [f'../gu/{quote("노원")}.html', '../gu/노원.html#real']
    assert receipt["changed_links"] == 1


@pytest.mark.parametrize("opening", ('<noscript id="noscript-real">', '<noscript id="noscript-real"/>'))
def test_script_enabled_noscript_markup_is_text_and_live_anchor_after_close_is_repaired(tmp_path, opening):
    from blog.archive_navigation import _ElementIds

    old = '../complex/노원-단지.html'
    inactive = opening + f'<a id="fake" href="{old}">fallback text</a></noscript>'
    real = f'<a id="after" href="{old}">live</a>'
    site, archive = _archive_site(tmp_path, inactive + real)
    receipt = _repair(site)
    assert archive.read_bytes() == (inactive + f'<a id="after" href="../gu/{quote("노원")}.html#{quote("단지")}">live</a>').encode("utf-8")
    assert receipt["changed_links"] == 1
    assert _ElementIds(inactive + real).ids == {"noscript-real", "after"}


def test_script_enabled_noscript_does_not_invent_fragment_but_preserves_own_id(tmp_path):
    current = '<noscript id="real"><b id="단지">fallback text</b></noscript>'
    source = '<a href="../complex/노원-단지.html">old</a><a href="../gu/노원.html#real">valid</a>'
    site, archive = _archive_site(tmp_path, source, current)
    receipt = _repair(site)
    assert _Links(archive.read_text()).links == [f'../gu/{quote("노원")}.html', '../gu/노원.html#real']
    assert receipt["scripting_policy"] == "enabled"


@pytest.fixture(scope="module")
def script_enabled_chromium_page():
    playwright_api = pytest.importorskip("playwright.sync_api")
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(java_script_enabled=True)
        context.route("**/*", lambda route: route.abort())
        page = context.new_page()
        yield page
        context.close()
        browser.close()


@pytest.mark.parametrize("opening,closing", (
    ('<plaintext id="real-text">', "</plaintext>"),
    ('<plaintext id="real-text"/>', "</plaintext>"),
    ('<noscript id="real-text">', "</noscript>"),
    ('<noscript id="real-text"/>', "</noscript>"),
))
def test_plaintext_and_noscript_parser_agree_with_actual_script_enabled_chromium(script_enabled_chromium_page, opening, closing):
    from blog.archive_navigation import _ArchiveAnchors, _ElementIds

    before = '<a id="before" href="../gu/노원.html">actual</a>'
    fake = '<a id="fake" href="../complex/노원-단지.html">text</a>'
    after = '<a id="after" href="../gu/노원.html">after</a>'
    source = '<!doctype html><html><head></head><body>' + before + opening + fake + closing + after + '</body></html>'
    page = script_enabled_chromium_page
    page.set_content(source)
    actual = page.evaluate("""() => ({
      hrefs: [...document.querySelectorAll('a[href]')].map(node => node.getAttribute('href')),
      ids: [...document.querySelectorAll('[id]')].map(node => node.id)
    })""")
    assert [href for href, _span, _error in _ArchiveAnchors(source).anchors] == actual["hrefs"]
    assert _ElementIds(source).ids == set(actual["ids"])
    assert "fake" not in actual["ids"]
