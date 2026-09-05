"""구 허브 단위테스트(2026-09-05 P1) — 집계게이트·전수표·P25/P75 정렬 회귀·GA4 토글·25구 빌드 배선.

회귀 배경: 2026-09-05 Playwright 스크린샷 리뷰에서 노원 허브 P25(5.73억) > P75(5.29억) 역전 발견 —
select_gated_medians 가 미정렬 리스트를 반환해 _pctile(정렬 입력 가정)에 그대로 흘러간 결함.
build_explorer.select_gated_medians 를 오름차순 정렬 반환으로 고쳤다 — 본 파일의
test_select_gated_medians_returns_sorted_list / test_p25_never_exceeds_p75_regardless_of_input_order 가 그 회귀를 고정한다.
"""
from __future__ import annotations
import json
import re
from urllib.parse import quote

import pytest

import blog.build_explorer as be
import blog.build_site as build_site
from blog.gu_hub import render_gu_hub, _eok


def _row(gu: str, name: str, **kw) -> dict:
    base = dict(gu=gu, name=name, saeng=f"{gu}구 어딘가", product_type="아파트",
                area_m2=59.0, molit_n=15, molit_recent_eok=10.0,
                molit_trend_dir=None, molit_trend_pct=None, molit_pos_52w=None,
                molit_p25_eok=None, molit_p75_eok=None,
                jeonse_n=None, jeonse_ratio_complex_pct=None, turnover_pct=None)
    base.update(kw)
    return base


def _cells_of(html_out: str, slug: str) -> list[str]:
    m = re.search(rf'<tr id="{re.escape(slug)}">(.*?)</tr>', html_out, re.DOTALL)
    assert m, f"row id={slug} not found"
    return re.findall(r"<td>(.*?)</td>", m.group(1), re.DOTALL)


# ── P25/P75 정렬 회귀(2026-09-05 발견·수정) ──────────────────────────────

def test_select_gated_medians_returns_sorted_list():
    rows = [_row("강남", f"단지{v}", molit_recent_eok=v) for v in (9.0, 3.0, 7.0, 1.0, 5.0)]
    vals = be.select_gated_medians(rows)
    assert vals == sorted(vals)


def test_p25_never_exceeds_p75_regardless_of_input_order():
    # 입력 순서를 값 기준 비정렬로(내림차순 섞어) — 과거 버그 재현 조건
    rows = [_row("노원", f"단지{v}", molit_recent_eok=v) for v in (9.0, 1.0, 8.0, 2.0, 7.0, 3.0, 6.0)]
    gated = be.select_gated_medians(rows)
    p25 = round(be._pctile(gated, 0.25), 2)
    p75 = round(be._pctile(gated, 0.75), 2)
    assert p25 <= p75
    out = render_gu_hub("노원", rows, "2026-09-04", "2026-09-05")
    m = re.search(r'P25.P75\(단지 중위 분포\)</span><span class=v[^>]*>(.*?)</span>', out)
    assert m, "P25-P75 타일을 찾지 못함"
    tile_text = m.group(1)
    assert _eok(p25) in tile_text
    assert _eok(p75) in tile_text


# ── 집계 게이트: 전수표에는 포함되지만 구중위/P25-P75 에는 제외 ─────────────

def test_ungated_outlier_excluded_from_aggregate_but_shown_in_table():
    rows = [
        _row("강남", "A", molit_recent_eok=10.0),
        _row("강남", "B", molit_recent_eok=12.0),
        _row("강남", "C", molit_recent_eok=8.0),
        _row("강남", "D_주상복합", product_type="주상복합", molit_recent_eok=1000.0),
    ]
    gu_med = be.compute_gu_median(rows)
    assert gu_med == 10.0     # [8,10,12] 만의 중위 — 1000 은 미포함
    out = render_gu_hub("강남", rows, "2026-09-04", "2026-09-05")
    assert "D_주상복합" in out                     # 전수표엔 표시
    m = re.search(r"구 중위\(중위의 중위\)</span><span class=v>([^<]+)</span>", out)
    assert m and m.group(1) == _eok(gu_med)         # 타일은 게이트 통과분만


def test_per_cell_jeonse_gate_shows_dash_when_undersampled():
    rows = [
        _row("강남", "전세통과", molit_n=12, jeonse_n=8, jeonse_ratio_complex_pct=70.0),
        _row("강남", "전세미달", molit_n=12, jeonse_n=2, jeonse_ratio_complex_pct=70.0),   # jeonse_n<5
    ]
    out = render_gu_hub("강남", rows, "2026-09-04", "2026-09-05")
    ok_cells = _cells_of(out, be.slugify_complex_name("전세통과"))
    bad_cells = _cells_of(out, be.slugify_complex_name("전세미달"))
    assert "70%" in ok_cells[7]
    assert bad_cells[7] == "—"


# ── 금칙어 가드 배선 ─────────────────────────────────────────────────────

def test_forbidden_word_in_complex_name_raises():
    rows = [_row("강남", "강남1위단지", molit_recent_eok=10.0)]
    with pytest.raises(ValueError, match="wording-guard"):
        render_gu_hub("강남", rows, "2026-09-04", "2026-09-05")


# ── GA4 토글 ────────────────────────────────────────────────────────────

def test_ga4_snippet_absent_when_measurement_id_empty(monkeypatch):
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "")
    rows = [_row("강남", "단지", molit_recent_eok=10.0)]
    out = render_gu_hub("강남", rows, "2026-09-04", "2026-09-05")
    assert "gtag" not in out


def test_ga4_snippet_present_with_custom_measurement_id(monkeypatch):
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "G-TESTHUB1")
    rows = [_row("강남", "단지", molit_recent_eok=10.0)]
    out = render_gu_hub("강남", rows, "2026-09-04", "2026-09-05")
    assert "G-TESTHUB1" in out and "gtag" in out


# ── build_site.build() 통합: 25구 허브 + sitemap + index + feed 배선 ────────

def test_build_site_wires_25_gu_hubs_sitemap_index_feed(tmp_path, monkeypatch):
    site_dir = tmp_path / "site"
    src_dir = tmp_path / "src"
    (src_dir / "daily").mkdir(parents=True)
    gu_names = [f"구{i:02d}" for i in range(25)]
    complexes = [_row(gu, f"{gu}단지", molit_recent_eok=10.0 + i) for i, gu in enumerate(gu_names)]
    ds = {"complexes": complexes, "count": len(complexes), "data_asof": "2026-09-04", "generated": "2026-09-05"}
    (src_dir / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")

    digest_title = "서울 아파트 오늘의 변화 — 2026-09-05 · 테스트다이제스트"
    digest_html = (f"<!DOCTYPE html><html><head><title>{digest_title}</title>"
                   '<meta name=description content="테스트 설명"></head><body>본문</body></html>')
    (src_dir / "daily" / "2026-09-05.html").write_text(digest_html, encoding="utf-8")
    (src_dir / "daily" / "latest.html").write_text(digest_html, encoding="utf-8")

    monkeypatch.setattr(build_site, "SITE", str(site_dir))
    monkeypatch.setattr(build_site, "SRC", str(src_dir))
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "G-TESTBUILD")

    build_site.build(today="2026-09-05")

    hub_files = sorted((site_dir / "gu").glob("*.html"))
    assert len(hub_files) == 25

    sitemap = (site_dir / "sitemap.xml").read_text(encoding="utf-8")
    for gu in gu_names:
        assert f"/gu/{quote(gu)}.html" in sitemap
    assert "/daily/2026-09-05.html" in sitemap
    assert "/daily/latest.html" in sitemap

    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    for gu in gu_names:
        assert f'gu/{quote(gu)}.html">{gu}</a>' in index_html
    assert "테스트다이제스트" in index_html          # 다이제스트 CTA
    assert "G-TESTBUILD" in index_html                # GA4

    methodology_html = (site_dir / "methodology.html").read_text(encoding="utf-8")
    assert "G-TESTBUILD" in methodology_html

    one_hub = (site_dir / "gu" / f"{gu_names[0]}.html").read_text(encoding="utf-8")
    assert "G-TESTBUILD" in one_hub

    feed = (site_dir / "feed.xml").read_text(encoding="utf-8")
    first_item = feed.split("<item>")[1]
    assert digest_title in first_item                # 다이제스트가 feed 첫 항목
