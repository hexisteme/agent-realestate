"""일간 다이제스트 단위테스트(2026-09-05 P1) — 게이트·바이트예산·태그화이트리스트·금칙어·슬러그링크 회귀방지.
대상: blog.daily_digest.build_daily_digest / _select_ranked / _gu_summary_rows, blog.tistory_draft.write_digest_draft.
"""
from __future__ import annotations
import re
from urllib.parse import quote

import pytest

import blog.complex_page as cp
from blog.build_site import BASE_URL
from blog.build_explorer import slugify_complex_name
from blog.daily_digest import build_daily_digest, _select_ranked, _gu_summary_rows
from blog.wording_guard import FORBIDDEN_WORDS
from blog.tistory_draft import TISTORY_TAGS, write_digest_draft
from blog.tistory_publish import _parse_helper

_ALLOWED_TAGS = {"p", "b", "br", "a", "span", "table", "tr", "td"}


def _row(gu: str, name: str, **kw) -> dict:
    base = dict(gu=gu, name=name, saeng=f"{gu}구 어딘가", product_type="아파트",
                area_m2=59.0, molit_n=15, molit_recent_eok=10.0,
                molit_trend_dir=None, molit_trend_pct=None, molit_pos_52w=None,
                jeonse_n=None, jeonse_ratio_complex_pct=None, gap_eok=None, turnover_pct=None)
    base.update(kw)
    return base


def _sample_ds() -> dict:
    rows = [
        _row("강남", "강남좋은아파트", molit_recent_eok=20.0, molit_pos_52w=100,
             molit_trend_dir="▲", molit_trend_pct=5.0),
        # 아래 3개는 각각 게이트 한 항목씩만 위반 — pos_52w=100 이라 게이트만 없으면 상단에 뜰 것들
        _row("강남", "강남주상복합게이트제외", product_type="주상복합", molit_recent_eok=25.0, molit_pos_52w=100),
        _row("강남", "강남좁은집게이트제외", area_m2=35.0, molit_recent_eok=10.0, molit_pos_52w=100),
        _row("강남", "강남적은표본게이트제외", molit_n=5, molit_recent_eok=10.0, molit_pos_52w=100),
        _row("서초", "서초하단단지", molit_recent_eok=8.0, molit_pos_52w=3,
             molit_trend_dir="▼", molit_trend_pct=-2.0),
        _row("강남", "강남전세단지", molit_n=12, molit_recent_eok=10.0, molit_pos_52w=50,
             jeonse_n=8, jeonse_ratio_complex_pct=80.0, gap_eok=2.0),
        _row("서초", "서초회전율단지", molit_n=20, molit_recent_eok=12.0, molit_pos_52w=50, turnover_pct=15.0),
    ]
    return {"complexes": rows, "count": len(rows), "data_asof": "2026-09-04", "generated": "2026-09-05"}


# ── 게이트: _select_ranked ───────────────────────────────────────────────

def test_select_ranked_excludes_jusang_narrow_and_low_n():
    sel = _select_ranked(_sample_ds())
    hi_names = {r["name"] for r in sel["hi"]}
    assert hi_names == {"강남좋은아파트"}          # 주상복합·40㎡미만·n<10 셋 다 제외
    assert {r["name"] for r in sel["lo"]} == {"서초하단단지"}
    assert {r["name"] for r in sel["jr"]} == {"강남전세단지"}
    assert {r["name"] for r in sel["tv"]} == {"서초회전율단지"}


def test_gu_summary_rows_one_per_distinct_gu_sorted():
    rows = _gu_summary_rows(_sample_ds())
    assert [r["gu"] for r in rows] == ["강남", "서초"]     # 정렬 + 구 개수만큼


# ── build_daily_digest 통합 ──────────────────────────────────────────────

def test_build_daily_digest_return_shape_and_title():
    d = build_daily_digest(_sample_ds(), "2026-09-05", "2026-09-04")
    assert set(d.keys()) == {"title", "tags", "tistory_html", "site_html", "summary"}
    assert d["title"] == "서울 아파트 오늘의 변화 — 2026-09-05 · 12개월 범위 상단 1곳·하단 1곳 · 7단지"
    assert d["tags"] == TISTORY_TAGS + ",오늘의변화"


def test_gated_out_rows_never_appear_in_either_html():
    d = build_daily_digest(_sample_ds(), "2026-09-05", "2026-09-04")
    for bad in ("강남주상복합게이트제외", "강남좁은집게이트제외", "강남적은표본게이트제외"):
        assert bad not in d["tistory_html"]
        assert bad not in d["site_html"]
    assert "강남좋은아파트" in d["tistory_html"]
    assert "강남전세단지" in d["tistory_html"]
    assert "서초회전율단지" in d["tistory_html"]


def test_tistory_html_byte_budget_and_tag_whitelist():
    d = build_daily_digest(_sample_ds(), "2026-09-05", "2026-09-04")
    tb = len(d["tistory_html"].encode("utf-8"))
    assert tb <= 30000
    tags_found = {m.lower() for m in re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", d["tistory_html"])}
    assert tags_found <= _ALLOWED_TAGS, f"허용 외 태그 발견: {tags_found - _ALLOWED_TAGS}"


def test_no_forbidden_words_in_either_html():
    d = build_daily_digest(_sample_ds(), "2026-09-05", "2026-09-04")
    for w in FORBIDDEN_WORDS:
        assert w not in d["tistory_html"], f"금칙어 '{w}' in tistory_html"
        assert w not in d["site_html"], f"금칙어 '{w}' in site_html"


def test_complex_name_links_to_gu_hub_anchor():
    d = build_daily_digest(_sample_ds(), "2026-09-05", "2026-09-04")
    expected = f'{BASE_URL}/gu/{quote("강남")}.html#{slugify_complex_name("강남좋은아파트")}'
    assert expected in d["tistory_html"]


def test_complex_page_gate_switches_digest_links_to_complex_page():
    """단지 개별 페이지 게이트(molit_n>=30) 통과 단지는 tistory/site 모두 ../complex/*.html 로,
    미통과(허브 게이트만 통과) 단지는 기존처럼 구허브 앵커로 링크된다(2026-09-05 P2)."""
    ds = {
        "complexes": [
            _row("강남", "강남게이트완전통과", molit_n=40, molit_recent_eok=20.0, molit_pos_52w=100,
                 molit_trend_dir="▲", molit_trend_pct=5.0),
            _row("강남", "강남허브만통과", molit_n=15, molit_recent_eok=20.0, molit_pos_52w=99,
                 molit_trend_dir="▲", molit_trend_pct=5.0),
        ],
        "count": 2, "data_asof": "2026-09-04", "generated": "2026-09-05",
    }
    d = build_daily_digest(ds, "2026-09-05", "2026-09-04")

    slug = cp.complex_slug("강남", "강남게이트완전통과")
    complex_href_abs = f'{BASE_URL}/complex/{quote(slug)}.html'
    complex_href_rel = f'../complex/{quote(slug)}.html'
    hub_href_abs = f'{BASE_URL}/gu/{quote("강남")}.html#{slugify_complex_name("강남허브만통과")}'
    hub_href_rel = f'../gu/{quote("강남")}.html#{slugify_complex_name("강남허브만통과")}'

    assert f'<a href="{complex_href_abs}">' in d["tistory_html"]
    assert f'<a href="{hub_href_abs}">' in d["tistory_html"]
    assert f'<a href="{complex_href_rel}">' in d["site_html"]
    assert f'<a href="{hub_href_rel}">' in d["site_html"]


def test_tistory_html_over_budget_raises():
    huge = [_row("강남", "강남좋은아파트", molit_recent_eok=20.0, molit_pos_52w=100)]
    huge += [{"gu": f"gu{i:04d}", "name": f"단지{i}"} for i in range(200)]   # 구별 요약만 부풀림
    ds = {"complexes": huge, "count": len(huge), "data_asof": "2026-09-04", "generated": "2026-09-05"}
    with pytest.raises(ValueError, match="예산 초과"):
        build_daily_digest(ds, "2026-09-05", "2026-09-04")


# ── write_digest_draft ↔ _parse_helper 왕복 ──────────────────────────────

def test_write_digest_draft_roundtrips_through_parse_helper(tmp_path):
    d = build_daily_digest(_sample_ds(), "2026-09-05", "2026-09-04")
    path = write_digest_draft(d, "2026-09-05", outdir=str(tmp_path))
    parsed = _parse_helper(path)
    assert parsed["title"] == d["title"]
    assert parsed["tags"] == d["tags"]
    assert parsed["body"] == d["tistory_html"]
