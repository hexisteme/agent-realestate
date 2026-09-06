"""구별 주간 리포트 발행 게이트(2026-09-06 P0) 단위테스트 — should_write_weekly_posts 의 요일/env
판정과, write_posts 의 스킵(포스트 0편이어도 llms.txt 는 항상 갱신)·강제발행(주간 제목·canonical·
claims.jsonl) 분기를 검증한다.
"""
from __future__ import annotations
import glob
import os

from blog.build_explorer import should_write_weekly_posts, write_posts


def test_should_write_weekly_posts_monday_gate():
    assert should_write_weekly_posts("2026-09-07") is True    # 월요일
    assert should_write_weekly_posts("2026-09-06") is False   # 일요일


def test_should_write_weekly_posts_env_override(monkeypatch):
    monkeypatch.setenv("RE_WEEKLY_POSTS", "1")
    assert should_write_weekly_posts("2026-09-06") is True    # 일요일이어도 강제 발행
    monkeypatch.setenv("RE_WEEKLY_POSTS", "0")
    assert should_write_weekly_posts("2026-09-07") is False   # 월요일이어도 강제 억제


def _row(gu: str, name: str) -> dict:
    return dict(gu=gu, name=name, saeng=f"{gu}구 어딘가", product_type="아파트",
                area_m2=59.0, pyeong=17.8, units=300, built_year=2010, molit_n=40,
                molit_recent_eok=10.0, molit_trend_dir=None, molit_trend_pct=None, molit_pos_52w=None,
                molit_p25_eok=None, molit_p75_eok=None,
                jeonse_n=None, jeonse_ratio_complex_pct=None, turnover_pct=None)


def _ds(today: str) -> dict:
    complexes = [_row("강남", "강남A"), _row("강남", "강남B"), _row("강남", "강남C"),
                 _row("노원", "노원A"), _row("노원", "노원B")]
    return {"complexes": complexes, "count": len(complexes),
            "data_asof": "2026-09-04", "generated": today}


def test_write_posts_skips_html_on_non_monday_but_still_writes_llms(tmp_path, monkeypatch):
    monkeypatch.delenv("RE_WEEKLY_POSTS", raising=False)
    ds = _ds("2026-09-06")   # 일요일 — 강제 오버라이드 없음
    out = write_posts(ds, str(tmp_path))
    assert glob.glob(str(tmp_path / "posts" / "*.html")) == []
    assert (tmp_path / "llms.txt").exists()
    assert len(out) == 2 and all(g["skipped"] for g in out)


def test_write_posts_forced_writes_weekly_titled_posts_with_canonical_and_claims(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_WEEKLY_POSTS", "1")
    ds = _ds("2026-09-06")   # 강제 발행 — 요일 무관
    out = write_posts(ds, str(tmp_path))
    assert not any(g.get("skipped") for g in out)

    posts = sorted(glob.glob(str(tmp_path / "posts" / "*.html")))
    assert len(posts) == 2   # 강남·노원 각 1편
    for p in posts:
        html_txt = open(p, encoding="utf-8").read()
        assert "주간 리포트" in html_txt
        assert 'rel="canonical"' in html_txt
        claims_path = p[:-5] + ".claims.jsonl"
        assert os.path.exists(claims_path)
        with open(claims_path, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        assert len(lines) >= 1
