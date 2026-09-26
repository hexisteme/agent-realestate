"""일간 다이제스트 단위테스트(2026-09-05 P1) — 게이트·바이트예산·태그화이트리스트·금칙어·슬러그링크 회귀방지.
대상: blog.daily_digest.build_daily_digest / _select_ranked / _gu_summary_rows, blog.tistory_draft.write_digest_draft.
"""
import importlib
import re
from urllib.parse import quote

import pytest

import blog.complex_page as cp
import blog.daily_digest
from blog.build_explorer import slugify_complex_name
from blog.build_site import BASE_URL
from blog.daily_digest import build_daily_digest, _gu_summary_rows, _select_ranked
from blog.tistory_draft import TISTORY_TAGS, write_digest_draft
from blog.tistory_publish import _parse_helper
from blog.wording_guard import FORBIDDEN_WORDS

_ALLOWED_TAGS = {"p", "b", "br", "a", "span", "table", "tr", "td"}


@pytest.fixture(autouse=True)
def _reload_daily_digest_module():
    importlib.reload(blog.daily_digest)


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


def _inventory(fresh=True, complete=True):
    districts = {
        "강남": {"total_article_count": 180, "sale_article_count": 120, "lease_article_count": 40,
                 "rent_article_count": 19, "short_term_rent_article_count": 1,
                 "physical_complex_count": 10, "complexes_with_sale_articles": 9},
        "서초": {"total_article_count": 130, "sale_article_count": 80, "lease_article_count": 30,
                 "rent_article_count": 20, "short_term_rent_article_count": 0,
                 "physical_complex_count": 8, "complexes_with_sale_articles": 7},
    }
    changes = {
        gu: {"total_article_count": {"delta": delta}}
        for gu, delta in (("강남", 5), ("서초", -3))
    }
    return {
        "status": "current", "fresh": fresh, "complete": complete,
        "observed_at": "2026-09-05T07:05:00+09:00", "districts": districts,
        "total": {"total_article_count": 310, "sale_article_count": 200, "lease_article_count": 70,
                  "rent_article_count": 39, "short_term_rent_article_count": 1,
                  "physical_complex_count": 18, "complexes_with_sale_articles": 16},
        "comparisons": {"1d": {"districts": changes}, "7d": {"districts": changes}},
    }


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
    assert d["title"] == "서울 아파트 실거래가 — 강남좋은아파트 등 상승 1단지 · 강남전세단지 갭 2억 (2026-09-05, 7단지)"
    assert all(t in d["tags"] for t in TISTORY_TAGS.split(","))
    assert "강남구아파트" in d["tags"]
    assert "서초구아파트" in d["tags"]
    assert "오늘의 서울 아파트 핵심 요약 (30초 브리핑)" in d["tistory_html"]
    assert "서울 아파트 인터랙티브 탐색기 열기" in d["tistory_html"]
    assert "매일 아침 자동 업데이트" in d["tistory_html"]


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


def test_listing_inventory_is_separate_full_district_table_and_compact_tistory():
    ds = _sample_ds()
    ds["listing_inventory"] = _inventory()
    d = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    assert "구별 네이버 표시 매물" in d["site_html"]
    assert "전체 표시건수" in d["site_html"]
    assert "+5" in d["site_html"] and "-3" in d["site_html"]
    assert "중개사 중복 노출" in d["site_html"]
    assert "25개 구 전체 1일·7일 표" in d["tistory_html"]
    assert "국토부 RTMS" in d["site_html"] and "네이버 법정동별 단지 목록" in d["site_html"]


def test_incomplete_inventory_never_exposes_partial_counts_or_deltas():
    ds = _sample_ds()
    ds["listing_inventory"] = _inventory(fresh=False, complete=False)
    d = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    assert "부분 합계와 증감은 공개하지 않습니다" in d["site_html"]
    assert "매매 표시건수" not in d["site_html"]
    assert "310" not in d["site_html"]


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


# ── 가격대별 요약(2026-09-07 4밴드) ─────────────────────────────────────

def test_band_summary_rows_and_tables_present():
    from blog.daily_digest import _band_summary_rows
    rows = _band_summary_rows(_sample_ds())
    assert [r["band"] for r in rows] == ["10억 미만", "10~15억", "15~20억", "20억 이상"]
    by = {r["band"]: r for r in rows}
    assert by["20억 이상"]["n"] == 2 and by["20억 이상"]["hi"] == 1      # 25억 주상복합은 단지 수엔 들고 게이트 집계엔 빠진다
    assert by["10억 미만"]["n"] == 1
    out = build_daily_digest(_sample_ds(), "2026-09-05", "2026-09-04")
    assert "가격대별 요약" in out["tistory_html"] and "가격대별 요약" in out["site_html"]


# ── 정보 아키텍처 및 시각적 위계 검증 ────────────────────────────────────

def test_tistory_information_hierarchy_order():
    ds = _sample_ds()
    ds["listing_inventory"] = _inventory()
    d = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    html = d["tistory_html"]

    idx_briefing = html.find("30초 브리핑")
    idx_today_num = html.find("오늘의 숫자")
    idx_hi = html.find("12개월 범위 상단 근접")
    idx_inventory = html.find("네이버 표시 매물")
    idx_gu = html.find("서울 25개 구 요약")
    idx_cta = html.find("인터랙티브 탐색기 열기")

    assert -1 < idx_briefing < idx_today_num < idx_hi, "실거래 핵심 정보가 최상단에 우선 배치되어야 합니다."
    assert idx_hi < idx_inventory < idx_gu < idx_cta, "매물 재고 및 25개 구, CTA가 올바른 순서로 배치되어야 합니다."


def test_tistory_visual_accent_colors():
    ds = _sample_ds()
    d = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    html = d["tistory_html"]
    # 상승/하락에 시각적 컬러(red/blue 계열) 스타일이 적용되어 있어야 함
    assert 'color:#dc2626' in html or 'color:#c43d2f' in html, "상승 지표에 시각적 레드 강조가 적용되어야 합니다."
    # 카카오 태그 화이트리스트 준수 검증
    tags_found = {m.lower() for m in re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", html)}
    assert tags_found <= _ALLOWED_TAGS


def test_build_digest_title_downside_market():
    from blog.daily_digest import _build_digest_title
    counts = {"n_total": 1000, "up": 100, "down": 450, "flat": 50}
    sel = {"hi": [], "lo": [{"name": "하락단지", "gu": "노원"}]}
    title = _build_digest_title("2026-09-26", counts, sel)
    assert "하락 450단지" in title
    assert "1년 저점대 1곳" in title


def test_build_digest_title_micro_intent():
    from blog.daily_digest import _build_digest_title
    ds = _sample_ds()
    sel = _select_ranked(ds)
    counts = {"n_total": 7, "up": 1, "down": 1, "flat": 0}
    title = _build_digest_title("2026-09-05", counts, sel)
    # 대표 고점 단지(강남좋은아파트) 및 갭단지(강남전세단지 갭 2억) 실명이 제목에 반영되어야 함
    assert "강남좋은아파트" in title
    assert "강남전세단지" in title
    assert "갭 2억" in title


def test_tistory_micro_budget_anchors():
    ds = _sample_ds()
    d = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    html = d["tistory_html"]
    # 예산대별 빠른 점프 앵커/인디케이터가 포함되어 있어야 함
    assert "10억 미만" in html
    assert "10~15억" in html
    assert "15~20억" in html
    assert "20억 이상" in html
    assert "예산대별 바로가기" in html or "가격대별 요약" in html




