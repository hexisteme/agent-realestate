"""sitemap.xml 사이트맵인덱스 분할(2026-09-05 P2) 단위테스트.
xml.etree 로 실제 파싱해 자식 sitemap-core/complex/posts.xml 각각의 URL 개수를 기대치와 맞춘다 —
문자열 포함검사만으로는 네임스페이스 붕괴·잘못된 XML 구조를 못 잡으므로 정규 파싱을 쓴다.
"""
from __future__ import annotations
import json
import xml.etree.ElementTree as ET
from urllib.parse import quote

import blog.build_site as build_site

NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _row(gu: str, name: str, **kw) -> dict:
    base = dict(gu=gu, name=name, saeng=f"{gu}구 어딘가", product_type="아파트",
                area_m2=59.0, pyeong=17.8, molit_n=40, molit_recent_eok=10.0,
                molit_trend_dir=None, molit_trend_pct=None, molit_pos_52w=None,
                molit_p25_eok=None, molit_p75_eok=None,
                jeonse_n=None, jeonse_ratio_complex_pct=None, turnover_pct=None)
    base.update(kw)
    return base


def _build_fixture(tmp_path, monkeypatch):
    site_dir = tmp_path / "site"
    src_dir = tmp_path / "src"
    (src_dir / "daily").mkdir(parents=True)
    (src_dir / "posts").mkdir(parents=True)

    complexes = [
        _row("강남", "강남게이트통과", molit_n=40, area_m2=59.0),
        _row("강남", "강남표본미달", molit_n=5, area_m2=59.0),
        _row("노원", "노원게이트통과", molit_n=35, area_m2=59.0),
        _row("서초", "서초비아파트", product_type="주상복합", molit_n=40),
    ]
    ds = {"complexes": complexes, "count": len(complexes),
          "data_asof": "2026-09-04", "generated": "2026-09-05"}
    (src_dir / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")

    digest_html = ("<!DOCTYPE html><html><head><title>테스트 다이제스트</title>"
                   '<meta name=description content="설명"></head><body>본문</body></html>')
    (src_dir / "daily" / "2026-09-05.html").write_text(digest_html, encoding="utf-8")
    (src_dir / "daily" / "latest.html").write_text(digest_html, encoding="utf-8")

    for i, gu in enumerate(("강남", "노원")):
        post_html = (f"<!DOCTYPE html><html><head><title>포스트{i}</title>"
                     '<meta name=description content="포스트 설명"></head><body>본문</body></html>')
        (src_dir / "posts" / f"2026-09-05-{gu}.html").write_text(post_html, encoding="utf-8")

    monkeypatch.setattr(build_site, "SITE", str(site_dir))
    monkeypatch.setattr(build_site, "SRC", str(src_dir))
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "G-TESTSITEMAP")

    # molit_path 를 존재하지 않는 파일로 고정 — 월별차트는 생략되지만(monthly=None) sitemap 카운트엔
    # 영향 없고, 실제 수 MB짜리 예시 MOLIT 파일을 매 테스트마다 로드하지 않아도 되어 빠르다.
    result = build_site.build(today="2026-09-05", molit_path=str(tmp_path / "no_such_molit.json"))
    return site_dir, result


def _urlset_locs(site_dir, filename) -> list[str]:
    root = ET.parse(site_dir / filename).getroot()
    assert root.tag == f"{NS}urlset"
    return [el.text for el in root.findall(f"{NS}url/{NS}loc")]


def test_sitemap_xml_is_a_valid_sitemapindex_with_three_children(tmp_path, monkeypatch):
    site_dir, _ = _build_fixture(tmp_path, monkeypatch)
    root = ET.parse(site_dir / "sitemap.xml").getroot()
    assert root.tag == f"{NS}sitemapindex"
    locs = [el.text for el in root.findall(f"{NS}sitemap/{NS}loc")]
    assert len(locs) == 3
    for fn in ("sitemap-core.xml", "sitemap-complex.xml", "sitemap-posts.xml"):
        assert any(loc.endswith(f"/{fn}") for loc in locs)
        assert (site_dir / fn).exists()


def test_sitemap_core_has_index_methodology_gu_hubs_and_digests(tmp_path, monkeypatch):
    site_dir, _ = _build_fixture(tmp_path, monkeypatch)
    locs = _urlset_locs(site_dir, "sitemap-core.xml")
    # 고정 2(랜딩+방법론) + 구허브 3(강남/노원/서초) + 다이제스트 2(날짜본+latest) = 7
    assert len(locs) == 7
    assert any(loc.endswith("/") for loc in locs)
    assert any(loc.endswith("/methodology.html") for loc in locs)
    assert any(loc.endswith("/daily/2026-09-05.html") for loc in locs)
    assert any(loc.endswith("/daily/latest.html") for loc in locs)
    for gu in ("강남", "노원", "서초"):
        assert any(f"/gu/{quote(gu)}.html" == loc[-len(f"/gu/{quote(gu)}.html"):] for loc in locs)


def test_sitemap_complex_has_only_gated_complexes(tmp_path, monkeypatch):
    site_dir, result = _build_fixture(tmp_path, monkeypatch)
    # 게이트(아파트·40㎡+·매매표본30건+) 통과는 강남게이트통과(n40)·노원게이트통과(n35) 2개뿐 —
    # 강남표본미달(n5<30)·서초비아파트(주상복합)는 제외.
    assert result["complex"] == 2
    locs = _urlset_locs(site_dir, "sitemap-complex.xml")
    assert len(locs) == 2
    assert any(quote("강남-강남게이트통과") in loc for loc in locs)
    assert any(quote("노원-노원게이트통과") in loc for loc in locs)


def test_sitemap_posts_matches_copied_post_files(tmp_path, monkeypatch):
    site_dir, result = _build_fixture(tmp_path, monkeypatch)
    assert result["posts"] == 2
    locs = _urlset_locs(site_dir, "sitemap-posts.xml")
    assert len(locs) == 2
    assert all("/posts/" in loc for loc in locs)


def test_robots_txt_points_at_sitemap_xml_not_a_child_file(tmp_path, monkeypatch):
    site_dir, _ = _build_fixture(tmp_path, monkeypatch)
    robots = (site_dir / "robots.txt").read_text(encoding="utf-8")
    assert "Sitemap: https://hexisteme.github.io/seoul-re-snapshot/sitemap.xml" in robots
    assert "sitemap-core.xml" not in robots
    assert "sitemap-complex.xml" not in robots
