"""단지 개별 페이지 단위테스트(2026-09-05 P2) — 게이트·slug·월별중위·peers·렌더 블록·탐색기 프리셋.
대상: blog.complex_page(select_page_complexes/complex_slug/select_peers/build_monthly_medians/
render_complex_page), blog.build_explorer.EXPLORER_HTML 의 프리셋 파서(deliverable 3).
"""
from __future__ import annotations
import functools
import http.server
import json
import threading
from urllib.parse import quote

import pytest

import blog.build_explorer as be
import blog.build_site as build_site
import blog.complex_page as cp


def _row(gu: str, name: str, **kw) -> dict:
    base = dict(gu=gu, name=name, saeng=f"{gu}-어딘가", product_type="아파트",
                area_m2=59.0, pyeong=17.8, units=1000, built_year=1990, decade="1990년대",
                area_band="~59㎡",
                molit_n=40, molit_recent_eok=6.0, molit_p25_eok=5.5, molit_p75_eok=6.5,
                molit_trend_dir="▲", molit_trend_pct=5.0, molit_pos_52w=70,
                pyeong_price_man=3000, price_segment="6~10억",
                jeonse_n=None, jeonse_ratio_complex_pct=None, jeonse_recent_eok=None, gap_eok=None,
                trade_annual=None, turnover_pct=None)
    base.update(kw)
    return base


def _sample_monthly() -> list[dict]:
    out = [{"ym": f"2025-{m:02d}", "n": 3, "median_eok": round(6.0 + m * 0.01, 2)} for m in range(9, 13)]
    out += [{"ym": f"2026-{m:02d}", "n": 2, "median_eok": round(6.2 + m * 0.01, 2)} for m in range(1, 9)]
    return out


# ── 게이트: select_page_complexes / passes_complex_page_gate ────────────────

def test_gate_requires_apartment_area40_n30():
    rows = [
        _row("강남", "통과", molit_n=30, area_m2=40.0, product_type="아파트"),
        _row("강남", "표본미달", molit_n=29),
        _row("강남", "면적미달", area_m2=39.9),
        _row("강남", "비아파트", product_type="주상복합"),
    ]
    gated = cp.select_page_complexes({"complexes": rows})
    assert {r["name"] for r in gated} == {"통과"}


def test_gate_boundary_values_inclusive_and_exclusive():
    assert cp.passes_complex_page_gate(_row("강남", "경계", molit_n=30, area_m2=40.0))
    assert not cp.passes_complex_page_gate(_row("강남", "n미달", molit_n=29, area_m2=40.0))
    assert not cp.passes_complex_page_gate(_row("강남", "면적미달", molit_n=30, area_m2=39.9))


# ── slug 안정성 ──────────────────────────────────────────────────────────

def test_complex_slug_strips_whitespace_and_prefixes_gu():
    assert cp.complex_slug("노원", "상계주공2단지") == "노원-상계주공2단지"
    assert cp.complex_slug("노원", " 상계 주공 2단지 ") == "노원-상계주공2단지"


def test_complex_slug_stable_and_gu_scoped():
    assert cp.complex_slug("강남", "래미안1차") == cp.complex_slug("강남", "래미안1차")
    assert cp.complex_slug("강남", "미래") != cp.complex_slug("강북", "미래")


# ── build_monthly_medians ────────────────────────────────────────────────

def test_monthly_medians_12_months_chronological_current_month_excluded():
    recs = [{"price": 600_000_000, "ym": "202509"}, {"price": 999_000_000, "ym": "202609"}]
    out = cp.build_monthly_medians(recs, "2026-09-05")
    assert len(out) == 12
    assert out[0]["ym"] == "2025-09" and out[-1]["ym"] == "2026-08"
    assert all(m["ym"] != "2026-09" for m in out)


def test_monthly_medians_empty_month_is_none_nonempty_has_value():
    recs = [{"price": 600_000_000, "ym": "202509"}]
    out = {m["ym"]: m for m in cp.build_monthly_medians(recs, "2026-09-05")}
    assert out["2025-09"] == {"ym": "2025-09", "n": 1, "median_eok": 6.0}
    assert out["2025-10"] == {"ym": "2025-10", "n": 0, "median_eok": None}


def test_monthly_medians_outlier_cut_applied_same_rule_as_median_of():
    # 전체중위=610M → 0.6배=366M. 100M 은 컷 대상, 나머지 둘은 살아남아 n=2.
    recs = [{"price": 600_000_000, "ym": "202608"}, {"price": 620_000_000, "ym": "202608"},
            {"price": 100_000_000, "ym": "202608"}]
    aug = next(m for m in cp.build_monthly_medians(recs, "2026-09-05") if m["ym"] == "2026-08")
    assert aug["n"] == 2
    assert aug["median_eok"] == 6.1


# ── select_peers ─────────────────────────────────────────────────────────

def test_peers_exclude_self_and_respect_area_window_sorted_by_median_distance():
    this_row = _row("노원", "상계주공2단지", area_m2=59.0, molit_recent_eok=6.49)
    gu_rows = [
        this_row,
        _row("노원", "벽산", area_m2=60.0, molit_recent_eok=5.6),        # |diff|=0.89, 안(±10)
        _row("노원", "동아불암", area_m2=68.0, molit_recent_eok=4.92),   # |diff|=1.57, 안(경계=9)
        _row("노원", "너무멀다", area_m2=100.0, molit_recent_eok=6.0),   # 밖(±10 초과) → 제외
        _row("노원", "중위없음", area_m2=59.0, molit_recent_eok=None),  # 중위 없음 → 제외
    ]
    peers = cp.select_peers(this_row, gu_rows)
    names = [p["name"] for p in peers]
    assert "상계주공2단지" not in names
    assert "너무멀다" not in names
    assert "중위없음" not in names
    assert names[0] == "벽산"   # 0.89 < 1.57


def test_peers_capped_at_8_sorted_ascending_by_distance():
    this_row = _row("강남", "기준", area_m2=59.0, molit_recent_eok=10.0)
    gu_rows = [this_row] + [_row("강남", f"단지{i}", area_m2=59.0, molit_recent_eok=10.0 + i)
                            for i in range(1, 12)]
    peers = cp.select_peers(this_row, gu_rows)
    assert len(peers) == 8
    diffs = [abs(p["molit_recent_eok"] - 10.0) for p in peers]
    assert diffs == sorted(diffs)


# ── render_complex_page: 필수 블록·JSON-LD·GA4·wording ────────────────────

def test_render_complex_page_contains_required_blocks_and_wording_ok():
    row = _row("노원", "상계주공2단지", jeonse_n=50, jeonse_ratio_complex_pct=41.6,
               jeonse_recent_eok=2.7, gap_eok=3.79, trade_annual=136.0, turnover_pct=6.7,
               subway_m=470, gongsi_man=35800, maint_fee_won=69642, parking_per_unit=0.39,
               _gu_median_eok=6.0)
    peers = [_row("노원", "벽산", area_m2=60.0, molit_recent_eok=5.6)]
    out = cp.render_complex_page(row, peers, _sample_monthly(), "2026-08-31", "2026-09-05")
    assert "<!DOCTYPE html>" in out
    assert "상계주공2단지" in out
    assert "BreadcrumbList" in out
    assert "Dataset" in out and "variableMeasured" in out
    assert "gtag" in out                        # GA4(기본 measurement id)
    assert "월별 중위 실거래가" in out              # 월별 차트 카드
    assert "전세와 얼마나 차이" in out
    assert "거래가 잦은 단지인가" in out
    assert "같은 구, 비슷한 전용" in out            # peers 카드
    assert "입지·단지" in out                     # facts 카드
    assert be.DISCLAIMER in out


def test_render_complex_page_monthly_none_shows_fallback_without_crash():
    row = _row("노원", "상계주공2단지")
    out = cp.render_complex_page(row, [], None, "2026-08-31", "2026-09-05")
    assert "생략" in out
    assert "<!DOCTYPE html>" in out


def test_render_complex_page_jeonse_gate_dash_when_undersampled():
    row = _row("노원", "전세미달", jeonse_n=2, jeonse_ratio_complex_pct=50.0)
    out = cp.render_complex_page(row, [], None, "2026-08-31", "2026-09-05")
    assert "전세 표본 5건 미만" in out


def test_forbidden_word_in_name_raises():
    row = _row("강남", "강남1위단지")
    with pytest.raises(ValueError, match="wording-guard"):
        cp.render_complex_page(row, [], None, "2026-08-31", "2026-09-05")


def test_ga4_snippet_absent_when_measurement_id_empty(monkeypatch):
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "")
    row = _row("강남", "단지")
    out = cp.render_complex_page(row, [], None, "2026-08-31", "2026-09-05")
    assert "gtag" not in out


def test_peers_link_to_complex_page_when_gated_else_hub_anchor():
    row = _row("노원", "기준", molit_n=40)
    peer_paged = _row("노원", "페이지있음", molit_n=40, area_m2=60.0, molit_recent_eok=6.0)
    peer_unpaged = _row("노원", "페이지없음", molit_n=5, area_m2=60.0, molit_recent_eok=6.1)
    out = cp.render_complex_page(row, [peer_paged, peer_unpaged], None, "2026-08-31", "2026-09-05")
    assert f'../complex/{quote(cp.complex_slug("노원", "페이지있음"))}.html' in out
    assert f'../gu/{quote("노원")}.html#{quote(be.slugify_complex_name("페이지없음"))}' in out


# ── 탐색기 프리셋 딥링크(deliverable 3) ──────────────────────────────────

def test_explorer_html_contains_preset_parser_and_query_keys():
    html_out = be.EXPLORER_HTML
    assert "function applyPreset" in html_out
    for key in ('"gu"', '"band"', '"sort"', '"dir"', '"q"'):
        assert key in html_out
    assert "copyStateLink" in html_out
    assert "applyPreset();init();render();" in html_out


def _serve_dir(path):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(path))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def test_playwright_explorer_gu_preset_filters_rows(tmp_path):
    # file:// 는 fetch(./dataset.json) 가 크로미움 보안정책에 막혀 항상 실패 → 로컬 HTTP 로 서빙.
    sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

    ds = {
        "disclaimer": "테스트 면책", "data_asof": "2026-09-04", "license": "CC-BY-NC-4.0",
        "takedown": "테스트 이의제기", "count": 3,
        "sources": [{"name": "국토부"}],
        "complexes": [
            _row("노원", "노원단지", molit_n=40),
            _row("강남", "강남단지", molit_n=40),
            _row("강남", "강남단지2", molit_n=40),
        ],
    }
    (tmp_path / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "explorer.html").write_text(be.EXPLORER_HTML, encoding="utf-8")

    httpd = _serve_dir(tmp_path)
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch()
            except Exception as e:
                pytest.skip(f"chromium 실행 불가: {e}")
            try:
                page = browser.new_page()
                page.goto(f"http://127.0.0.1:{httpd.server_address[1]}/explorer.html?gu=노원")
                page.wait_for_selector("#tbody tr")
                rows_text = page.locator("#tbody tr").all_inner_texts()
            finally:
                browser.close()
    finally:
        httpd.shutdown()

    assert len(rows_text) == 1
    assert "노원단지" in rows_text[0]
