"""build_site 단지 개별 페이지 정리(prune) 회귀테스트(2026-09-06).

배경: 단지가 구 이동(소재구 확정, collect_frame_district.py)이나 게이트(n≥30) 탈락으로 slug 이
바뀌면 site/complex/*.html 옛 파일이 남아 사이트맵(complex/*.html glob)에 계속 실렸다. build_site.build
는 이번 회차에 실제로 쓴 slug 집합에 없는 site/complex/*.html 을 전부 지운다 — 이 정리 규칙을 고정한다.

tests/test_gu_hub.py::test_build_site_wires_25_gu_hubs_sitemap_index_feed 와 동일한 관례로 SITE/SRC 를
tmp_path 로 monkeypatch 해 build_site.build() 를 실제로 실행한다(정리 로직만 떼어 재구현하지 않음 —
프로덕션 코드 리팩터 없이 검증 가능한 최소 경로)."""
from __future__ import annotations

import json

import blog.build_site as build_site
import blog.complex_page as cp


def _row(gu: str, name: str, **kw) -> dict:
    # tests/test_complex_page.py 의 _row 와 동일 필드셋 — 기본값이 이미 단지 개별 페이지 게이트
    # (아파트·40㎡+·매매표본30+)를 통과해 render_complex_page 전체 파이프라인이 실제로 실행된다.
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


def test_build_site_prunes_stale_complex_pages_not_written_this_round(tmp_path, monkeypatch):
    """정리 규칙 회귀 고정 — 이번 회차 dataset.json 이 만드는 slug 집합에 없는 기존
    site/complex/*.html 은 삭제되고, 이번에 실제로 쓴 slug 파일은 남아야 한다."""
    site_dir = tmp_path / "site"
    src_dir = tmp_path / "src"
    (src_dir / "daily").mkdir(parents=True)

    row = _row("노원", "잔존단지")
    ds = {"complexes": [row], "count": 1, "data_asof": "2026-09-04", "generated": "2026-09-05"}
    (src_dir / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")

    (site_dir / "complex").mkdir(parents=True)
    stale = site_dir / "complex" / "노원-옛단지.html"          # 이전 회차 산출물 — 이번 dataset 엔 없음(구 이동/게이트 탈락 가정)
    stale.write_text("<html>옛 단지</html>", encoding="utf-8")
    survivor_path_before = site_dir / "complex" / f"{cp.complex_slug('노원', '잔존단지')}.html"
    survivor_path_before.write_text("<html>지난 회차 스냅샷(이번에 재작성됨)</html>", encoding="utf-8")

    monkeypatch.setattr(build_site, "SITE", str(site_dir))
    monkeypatch.setattr(build_site, "SRC", str(src_dir))
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "G-TESTPRUNE")

    # molit_path 미존재 — 월별차트 생략 경로(render_complex_page 는 monthly=None 을 안내문으로 대체, test_complex_page.py 검증됨)
    build_site.build(today="2026-09-05", molit_path=str(tmp_path / "no_such_molit.json"))

    remaining = {p.name for p in (site_dir / "complex").glob("*.html")}
    expected = f"{cp.complex_slug('노원', '잔존단지')}.html"
    assert remaining == {expected}, f"옛 파일이 남았거나 이번 회차 파일이 없음: {remaining}"
    # 살아남은 파일은 "삭제되지 않은 것"이 아니라 이번 회차에 실제로 재작성된 것이어야 함(내용 갱신 확인)
    assert "지난 회차 스냅샷" not in (site_dir / "complex" / expected).read_text(encoding="utf-8")


def test_build_site_keeps_complex_page_absent_when_no_complex_gates_this_round(tmp_path, monkeypatch):
    """반대 방향 회귀 — 이번 회차에 게이트 통과 단지가 하나도 없으면(전수 탈락) site/complex 의
    옛 파일도 전부 정리되어야 한다(빈 written_slugs 집합에 아무것도 안 남으면 안 됨을 방지)."""
    site_dir = tmp_path / "site"
    src_dir = tmp_path / "src"
    (src_dir / "daily").mkdir(parents=True)

    row = _row("노원", "게이트미달단지", molit_n=5)   # n<30 — 개별 페이지 게이트 탈락
    ds = {"complexes": [row], "count": 1, "data_asof": "2026-09-04", "generated": "2026-09-05"}
    (src_dir / "dataset.json").write_text(json.dumps(ds, ensure_ascii=False), encoding="utf-8")

    (site_dir / "complex").mkdir(parents=True)
    stale = site_dir / "complex" / "노원-옛단지.html"
    stale.write_text("<html>옛 단지</html>", encoding="utf-8")

    monkeypatch.setattr(build_site, "SITE", str(site_dir))
    monkeypatch.setattr(build_site, "SRC", str(src_dir))
    monkeypatch.setattr(build_site, "GA4_MEASUREMENT_ID", "G-TESTPRUNE2")

    build_site.build(today="2026-09-05", molit_path=str(tmp_path / "no_such_molit.json"))

    remaining = list((site_dir / "complex").glob("*.html"))
    assert remaining == [], f"게이트 전수탈락인데 옛 파일이 남음: {remaining}"
