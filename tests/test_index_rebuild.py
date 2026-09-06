"""인덱스 재구성(2026-09-06 P0) 회귀테스트 — BLOG_SITE_DIR/BLOG_SRC_DIR 환경변수 오버라이드가
blog.build_site 의 모듈전역 SITE/SRC 를 실제로 바꾸는지(env 를 먼저 세팅 → import/reload 순서),
그리고 그 위에서 조립된 index.html 이 예산(<60KB)·25구 타일·아카이브 분리·GA4·면책 문구를,
sitemap-core.xml/feed.xml 이 archive.html·일간 다이제스트 배선을 갖는지 검증한다.

SITE/SRC 는 build_site.py 상단에서 `os.environ.get(...)`으로 읽는 모듈전역이라, 이미 다른 테스트가
`import blog.build_site`를 끝낸 뒤(pytest 수집 단계에서 흔함)라면 env 만 바꿔서는 반영되지 않는다 —
env 를 먼저 세팅한 뒤 importlib.reload 로 재실행해야 오버라이드가 걸린다. 테스트 종료 후에는 env 를
지우고 다시 reload 해 다른 테스트가 보는 모듈 상태를 실 기본값(site/report/blog)으로 원복한다.
"""
from __future__ import annotations
import importlib
import json
import os

GU25 = ["강남", "강동", "강북", "강서", "관악", "광진", "구로", "금천", "노원", "도봉", "동대문", "동작",
        "마포", "서대문", "서초", "성동", "성북", "송파", "양천", "영등포", "용산", "은평", "종로", "중구", "중랑"]


def _row(gu: str, name: str) -> dict:
    """게이트 우회용 최소 행 — molit_n=10(<30 매매표본 게이트) 이라 단지 개별페이지(complex_page.py,
    다른 워커 동시편집분)를 이 테스트가 건드리지 않는다. 필드셋은 tests/test_sitemap_index.py 의
    검증된 _row 와 동일 계열(+units/built_year — build_gu_claims 가 직접 인덱싱하므로 필수)."""
    return dict(gu=gu, name=name, saeng=f"{gu}구 어딘가", product_type="아파트",
                area_m2=59.0, pyeong=17.8, units=300, built_year=2010, molit_n=10,
                molit_recent_eok=10.0, molit_trend_dir=None, molit_trend_pct=None, molit_pos_52w=None,
                molit_p25_eok=None, molit_p75_eok=None,
                jeonse_n=None, jeonse_ratio_complex_pct=None, turnover_pct=None)


def _build_index(tmp_path):
    site_dir = tmp_path / "site"
    src_dir = tmp_path / "src"
    (src_dir / "daily").mkdir(parents=True)
    (src_dir / "posts").mkdir(parents=True)

    complexes = [_row(gu, f"{gu}테스트단지{i}") for gu in GU25 for i in range(2)]
    ds = {"complexes": complexes, "count": len(complexes),
          "data_asof": "2026-09-04", "generated": "2026-09-05"}
    (src_dir / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")
    (src_dir / "explorer.html").write_text("<!doctype html><html><body>explorer stub</body></html>",
                                            encoding="utf-8")

    # 날짜를 서로 다르게(포스트 09-01/09-03, 다이제스트 09-05=today) 둬서 feed.xml 최신순 병합을
    # 실제로 검증할 수 있게 한다 — 전부 같은 날짜면 union 정렬이 안정정렬로 우연히 통과할 수 있다.
    post_a = ("<!DOCTYPE html><html><head><title>포스트0-강남</title>"
              '<meta name=description content="강남 포스트 설명"></head><body>본문</body></html>')
    post_b = ("<!DOCTYPE html><html><head><title>포스트1-노원</title>"
              '<meta name=description content="노원 포스트 설명"></head><body>본문</body></html>')
    (src_dir / "posts" / "2026-09-01-강남.html").write_text(post_a, encoding="utf-8")
    (src_dir / "posts" / "2026-09-03-노원.html").write_text(post_b, encoding="utf-8")

    digest_html = ("<!DOCTYPE html><html><head><title>다이제스트-테스트</title>"
                   '<meta name=description content="오늘의 변화 요약"></head><body>본문</body></html>')
    (src_dir / "daily" / "2026-09-05.html").write_text(digest_html, encoding="utf-8")

    os.environ["BLOG_SITE_DIR"] = str(site_dir)
    os.environ["BLOG_SRC_DIR"] = str(src_dir)
    import blog.build_site as build_site
    importlib.reload(build_site)
    try:
        result = build_site.build(today="2026-09-05", molit_path=str(tmp_path / "no_such_molit.json"))
    finally:
        del os.environ["BLOG_SITE_DIR"]
        del os.environ["BLOG_SRC_DIR"]
        importlib.reload(build_site)   # 다른 테스트를 위해 모듈 상태를 실 기본값으로 원복
    return build_site, site_dir, result


def test_index_rebuild_env_override_budget_and_archive_split(tmp_path):
    build_site, site_dir, result = _build_index(tmp_path)
    assert result["posts"] == 2

    idx = (site_dir / "index.html").read_text(encoding="utf-8")
    assert len(idx.encode("utf-8")) < 60_000
    assert idx.count('href="gu/') == 25          # 25구 타일 전부
    assert "archive.html" in idx
    assert "투자자문·매수권유가 아닙니다" in idx
    assert build_site.GA4_MEASUREMENT_ID in idx

    archive = (site_dir / "archive.html").read_text(encoding="utf-8")
    assert "2026-09-01-강남" in archive
    assert "2026-09-03-노원" in archive


def test_sitemap_core_lists_archive(tmp_path):
    build_site, site_dir, _ = _build_index(tmp_path)
    core = (site_dir / "sitemap-core.xml").read_text(encoding="utf-8")
    assert "archive.html" in core


def test_feed_merges_posts_and_daily_newest_first(tmp_path):
    build_site, site_dir, _ = _build_index(tmp_path)
    feed = (site_dir / "feed.xml").read_text(encoding="utf-8")
    assert "/daily/2026-09-05.html" in feed
    assert "/posts/2026-09-01-%EA%B0%95%EB%82%A8.html" in feed or "2026-09-01-강남" in feed
    i_daily = feed.find("다이제스트-테스트")
    i_post_no = feed.find("포스트1-노원")
    i_post_gn = feed.find("포스트0-강남")
    assert -1 not in (i_daily, i_post_no, i_post_gn)
    assert i_daily < i_post_no < i_post_gn        # 09-05 > 09-03 > 09-01 최신순
    assert "<description>다이제스트-테스트</description>" in feed   # 다이제스트는 desc=title
