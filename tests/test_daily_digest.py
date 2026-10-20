"""일간 다이제스트 단위테스트(2026-09-05 P1) — 게이트·바이트예산·태그화이트리스트·금칙어·슬러그링크 회귀방지.
대상: blog.daily_digest.build_daily_digest / _select_ranked / _gu_summary_rows, blog.tistory_draft.write_digest_draft.
"""
import importlib
import html as html_lib
from html.parser import HTMLParser
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

import pytest

import blog.complex_page as cp
import blog.daily_digest
from blog.build_explorer import slugify_complex_name
from blog.build_site import BASE_URL
from blog.daily_digest import build_daily_digest, _gu_summary_rows, _select_ranked
from blog.tistory_draft import TISTORY_TAGS, write_digest_draft
from blog.tistory_publish import _parse_helper
from blog.wording_guard import FORBIDDEN_WORDS

_ALLOWED_TAGS = {"p", "b", "br", "a", "span", "table", "tr", "td", "h3", "h4"}  # h3·h4: 본문 절 제목(스킨 글 제목 h2 아래, design-review 2026-10-09 floker-114 B-3)


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


def _answer_first_ds():
    """합성 관측: 회전율 15/10/6/5%, 범위 하단 1/3/5/6%는 독립 literal 기대값."""
    rows = [
        _row("강남", "합성회전넷", turnover_pct=5, molit_n=25),
        _row("강남", "합성회전둘", turnover_pct=10, molit_n=50),
        _row("강남", "합성회전셋", turnover_pct=6, molit_n=30),
        _row("강남", "합성회전하나", turnover_pct=15, molit_n=75),
        _row("서초", "합성하단넷", molit_pos_52w=6, molit_n=16),
        _row("서초", "합성하단둘", molit_pos_52w=3, molit_n=13),
        _row("서초", "합성하단셋", molit_pos_52w=5, molit_n=15),
        _row("서초", "합성하단하나", molit_pos_52w=1, molit_n=11),
        _row("강남", "합성상단", molit_pos_52w=100, molit_trend_dir="▲", molit_trend_pct=5),
        _row("강남", "합성전세", jeonse_n=8, jeonse_ratio_complex_pct=80, gap_eok=2),
        _row("강남", "합성제외유형", product_type="주상복합", turnover_pct=99, molit_pos_52w=0),
        _row("강남", "합성제외면적", area_m2=35, turnover_pct=98, molit_pos_52w=0),
        _row("강남", "합성제외표본", molit_n=9, turnover_pct=97, molit_pos_52w=0),
    ]
    gus = ("강남", "강동", "강북", "강서", "관악", "광진", "구로", "금천", "노원", "도봉", "동대문",
           "동작", "마포", "서대문", "서초", "성동", "성북", "송파", "양천", "영등포", "용산", "은평",
           "종로", "중구", "중랑")
    rows += [_area_row(gu=gu, name=f"합성평형{gu}", complex_no=f"synthetic-{i}")
             for i, gu in enumerate(gus)]
    return {"complexes": rows, "count": len(rows), "data_asof": "2026-09-04"}


def _visible_digest_text(body):
    # Navigation repeats section names; ordering assertions must inspect section content.
    body = re.sub(r'<p\b[^>]*\bdata-reading-toc="1"[^>]*>.*?</p>', '', body, flags=re.S)
    class VisibleText(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []
            self.hidden = 0

        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style"}:
                self.hidden += 1

        def handle_endtag(self, tag):
            if tag in {"script", "style"}:
                self.hidden -= 1

        def handle_data(self, data):
            if not self.hidden:
                self.parts.append(data)

    parser = VisibleText()
    parser.feed(body)
    return " ".join(" ".join(parser.parts).split())


def _assert_answer_first(body, surface):
    """Catch missing/reordered summaries, wrong selected rows/metrics, and premature final CTA."""
    text = _visible_digest_text(body)
    question = "서울 아파트 매수를 검토할 때, 어떤 실거래 관측부터 확인하면 좋을까요?"
    tv_heading = "12개월 회전율 상위 관측"
    lo_heading = "52주 범위 하단 근접 관측"
    briefing = "30초 브리핑" if surface == "tistory_html" else "이 페이지에서만 알 수 있는 것"
    assert question in text
    assert text.index(question) < text.index(tv_heading) < text.index(lo_heading) < text.index(briefing)
    tv = text.split(tv_heading, 1)[1].split(lo_heading, 1)[0]
    lo = text.split(lo_heading, 1)[1].split(briefing, 1)[0]
    for section, names, metrics, counts in (
        (tv, ("합성회전하나", "합성회전둘", "합성회전셋"),
         ("회전율 15%", "회전율 10%", "회전율 6%"), ("75", "50", "30")),
        (lo, ("합성하단하나", "합성하단둘", "합성하단셋"),
         ("52주 범위 위치 1%", "52주 범위 위치 3%", "52주 범위 위치 5%"), ("11", "13", "15")),
    ):
        assert "3개 관측" in section
        assert section.index(names[0]) < section.index(names[1]) < section.index(names[2])
        for name, metric, count in zip(names, metrics, counts):
            row = section.split(name, 1)[1].split("건", 1)[0] + "건"
            assert metric in row
            assert ("가격 표본" if "회전율" in row else "12개월 가격 표본") + f" {count}건" in row
    assert "합성회전넷" not in tv
    assert "합성하단넷" not in lo
    assert not any(name in tv + lo for name in ("합성제외유형", "합성제외면적", "합성제외표본"))
    detail = text.index("12개월 범위 상단 근접")
    gu_heading = "서울 25개 구 요약" if surface == "tistory_html" else "구별 요약 (25개 구)"
    gu = text.index(gu_heading)
    disclaimer = text.index("투자자문·매수권유가 아닙니다")
    cta = text.index("인터랙티브 탐색기 열기")
    assert text.index(briefing) < detail < gu < text.index("게이트:") < disclaimer < cta
    assert gu < text.index("방법론 전문") < cta
    assert "59㎡·84㎡ 실거래 중위 관측" in text
    assert "전세가율 상위" in text and "회전율 상위" in text
    assert "국토부 RTMS" in text and "표본수" in text
    for unsupported in ("주간 거래량", "저평가", "대출 규제 영향", "10년 이력", "대지지분"):
        assert unsupported not in text
    final_link = re.search(r'<a href="([^"]+)"[^>]*>[^<]*인터랙티브 탐색기 열기[^<]*</a>', body)
    assert final_link
    query = parse_qs(urlsplit(html_lib.unescape(final_link.group(1))).query)
    assert query["utm_campaign"] == ["realestate-daily"]
    assert query["utm_content"] == ["daily:2026-09-05"]
    assert query["utm_source"] == ["tistory" if surface == "tistory_html" else "owned_daily"]
    assert query["utm_medium"] == ["referral" if surface == "tistory_html" else "internal"]


def test_daily_keeps_support_guidance_and_macro_through_budget_compaction():
    """Dropping housing_support or macro_html in a compaction pass loses required reader information."""
    rows = [_area_row(gu=f"구{i:02}", name=f"긴이름평형단지{i:02}", complex_no=str(i),
                      molit_pos_52w=100, turnover_pct=15, jeonse_n=8,
                      jeonse_ratio_complex_pct=80, gap_eok=2) for i in range(25)]
    ds = {"complexes": rows, "count": 25, "listing_inventory": _inventory()}
    support = {"schema_version": 1, "target_date": "2026-10-03", "collected_at": None,
               "sources": {}, "notices": [], "errors": {}}
    macro = {"tistory_html": "<p>거시 지표 · 한국은행 기준금리 3.00%</p>",
             "site_html": "<h2>거시 지표</h2><p>한국은행 기준금리 3.00%</p>"}
    digest = build_daily_digest(ds, "2026-10-03", "2026-10-01", macro=macro, housing_support=support)
    for surface in ("tistory_html", "site_html"):
        text = _visible_digest_text(digest[surface])
        for required in ("SH", "LH", "청약홈", "청년", "신혼", "무순위", "한국은행 기준금리 3.00%"):
            assert required in text
        assert "housing-support.html" in digest[surface]
    assert len(digest["tistory_html"].encode()) <= 28000
    for i in range(25):
        assert f"<b>구{i:02}</b>" in digest["tistory_html"]


@pytest.mark.parametrize("surface", ["tistory_html", "site_html"])
def test_answer_first_uses_observed_top_three_before_details_and_final_cta(surface):
    digest = build_daily_digest(_answer_first_ds(), "2026-09-05", "2026-09-04")
    assert set(digest) == {"title", "tags", "tistory_html", "site_html", "summary"}
    _assert_answer_first(digest[surface], surface)


@pytest.mark.parametrize("surface", ["tistory_html", "site_html"])
@pytest.mark.parametrize("available", [0, 1, 2])
def test_answer_first_reports_only_available_observations(surface, available):
    rows = [_row("강남", f"합성소수{i}", turnover_pct=10-i, molit_pos_52w=i+1) for i in range(available)]
    digest = build_daily_digest({"complexes": rows, "count": len(rows)}, "2026-09-05", "2026-09-04")
    text = _visible_digest_text(digest[surface])
    assert "12개월 회전율 상위 관측" in text
    assert "52주 범위 하단 근접 관측" in text
    answer = text.split("12개월 회전율 상위 관측", 1)[1].split("오늘의 숫자", 1)[0]
    if available:
        assert answer.count(f"{available}개 관측") == 2
        assert "3개 관측" not in answer
        for i in range(available):
            assert answer.count(f"합성소수{i}") == 2
    else:
        assert answer.count("기준 충족 관측 없음") == 2


@pytest.mark.parametrize("surface", ["tistory_html", "site_html"])
@pytest.mark.parametrize("present_group", ["turnover", "bottom"])
def test_answer_first_empty_group_does_not_borrow_other_groups_candidates(surface, present_group):
    row = _row("강남", "합성한쪽", **({"turnover_pct": 15} if present_group == "turnover" else {"molit_pos_52w": 1}))
    digest = build_daily_digest({"complexes": [row], "count": 1}, "2026-09-05", "2026-09-04")
    text = _visible_digest_text(digest[surface])
    tv = text.split("12개월 회전율 상위 관측", 1)[1].split("52주 범위 하단 근접 관측", 1)[0]
    lo = text.split("52주 범위 하단 근접 관측", 1)[1].split("회전율은", 1)[0]
    present, absent = (tv, lo) if present_group == "turnover" else (lo, tv)
    assert "1개 관측" in present and "합성한쪽" in present
    assert "0개 관측" in absent and "기준 충족 관측 없음" in absent
    assert "합성한쪽" not in absent


@pytest.mark.parametrize("surface", ["tistory_html", "site_html"])
def test_answer_first_complex_name_stays_literal_text(surface):
    name = '합성&"관측<표본>'
    row = _row("강남", name, turnover_pct=15, molit_pos_52w=1)
    digest = build_daily_digest({"complexes": [row], "count": 1}, "2026-09-05", "2026-09-04")
    text = _visible_digest_text(digest[surface])
    answer = text.split("12개월 회전율 상위 관측", 1)[1].split("회전율은", 1)[0]
    assert answer.count(name) == 2


def test_answer_first_survives_detail_budget_degradation(monkeypatch):
    ds = _answer_first_ds()
    ds["listing_inventory"] = _inventory()
    # Fill repeated tables, never the required summary; literal candidates still lead both groups.
    ds["complexes"] += [_area_row(gu="강남", name=f"합성반복상세{i}", complex_no=f"detail-{i}",
                                   molit_pos_52w=100, turnover_pct=4, jeonse_n=8,
                                   jeonse_ratio_complex_pct=70, gap_eok=3) for i in range(8)]
    ds["count"] = len(ds["complexes"])
    # The responsive layout is smaller; force an actual compaction instead of pinning a row count.
    monkeypatch.setattr(blog.daily_digest, "_TISTORY_TEXT_BUDGET", 22000)
    digest = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    assert "아래 반복 상세 표는 각 최대" in digest["tistory_html"]
    assert len(digest["tistory_html"].encode("utf-8")) <= 22000
    _assert_answer_first(digest["tistory_html"], "tistory_html")


def test_answer_first_final_files_roundtrip_through_actual_daily_output(tmp_path):
    from pathlib import Path
    from blog.run_daily import write_digest_outputs
    from blog.tistory_contract import parse_helper
    digest = build_daily_digest(_answer_first_ds(), "2026-09-05", "2026-09-04")
    paths = write_digest_outputs(digest, "2026-09-05", str(tmp_path), ds=None)
    parsed = parse_helper(paths["draft"])
    assert parsed == {"title": digest["title"], "tags": digest["tags"], "body": digest["tistory_html"]}
    _assert_answer_first(parsed["body"], "tistory_html")
    dated = Path(paths["dated"]).read_text(encoding="utf-8")
    assert dated == Path(paths["latest"]).read_text(encoding="utf-8") == digest["site_html"]
    _assert_answer_first(dated, "site_html")


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
    assert d["title"] == "서울 아파트 실거래가 — 비교 2단지, 최근 3개월 vs 직전 9개월 (2026-09-05)"
    assert all(t in d["tags"] for t in TISTORY_TAGS.split(","))
    assert "강남구아파트" in d["tags"]
    assert "서초구아파트" in d["tags"]
    assert "오늘의 서울 아파트 핵심 요약 (30초 브리핑)" in d["tistory_html"]
    assert "서울 아파트 인터랙티브 탐색기 열기" in d["tistory_html"]
    assert "매일 아침 자동 업데이트" in d["tistory_html"]
    assert '<h1>서울 집값과 주거지원, 한눈에</h1>' in d["site_html"]
    assert f"<title>{d['title']}</title>" in d["site_html"]
    assert '<link rel="canonical" href="https://hexisteme.github.io/seoul-re-snapshot/daily/2026-09-05.html">' in d["site_html"]
    assert "서울 관측 7단지 직접 필터·비교하기" in d["tistory_html"]   # Codex X3 — 주상복합·도생 포함이라 '아파트 전체' 아님
    assert "utm_source=tistory" in d["tistory_html"]
    assert "utm_source=owned_daily" in d["site_html"]
    assert "acquisition_cta_click" in d["site_html"]
    assert "1,759단지" not in d["tistory_html"]


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
    assert tb <= 28000  # native 대표이미지 메타 2,000B를 30KB에서 예약
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


@pytest.mark.parametrize("name", ["앵커 검증", "앵커&검증", '앵커"검증', "앵커%20검증"])
def test_daily_gu_fragment_decodes_to_an_actual_rendered_hub_id(name):
    """Catch single-encoded URLs targeting literal percent-encoded HTML ids."""
    from blog.gu_hub import render_gu_hub
    class HubIds(HTMLParser):
        def __init__(self):
            super().__init__()
            self.values = set()

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == "tr" and values.get("id"):
                self.values.add(values["id"])

    row = _row("노원", name, molit_pos_52w=100)
    ds = {"complexes": [row], "count": 1, "data_asof": "2026-09-04"}
    hub = render_gu_hub("노원", [row], "2026-09-04", "2026-09-05", ds=ds)
    ids = HubIds()
    ids.feed(hub)
    digest = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    for surface in ("tistory_html", "site_html"):
        links = re.findall(r'<a href="([^"]+)"', digest[surface])
        fragments = [urlsplit(html_lib.unescape(link)).fragment for link in links
                     if "/gu/" in link and urlsplit(link).fragment]
        assert fragments
        assert all(unquote(fragment) in ids.values for fragment in fragments)


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
    html = re.sub(r'<p\b[^>]*\bdata-reading-toc="1"[^>]*>.*?</p>', '', d["tistory_html"], flags=re.S)

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
    counts = {"n_total": 1000, "n_judged": 550, "up": 100, "down": 450, "flat": 0}
    sel = {"hi": [], "lo": [{"name": "하락단지", "gu": "노원"}]}
    title = _build_digest_title("2026-09-26", counts, sel)
    assert title == "서울 아파트 실거래가 — 비교 550단지, 최근 3개월 vs 직전 9개월 (2026-09-26)"


def test_build_digest_title_names_no_complex():
    """외부 리뷰(2026-10-13) C2 — 제목에 단지명·매매·전세 중위 차이를 올리지 않는다(2026-09-26 마이크로 인텐트 폐기)."""
    from blog.daily_digest import _build_digest_title
    ds = _sample_ds()
    sel = _select_ranked(ds)
    title = _build_digest_title("2026-09-05", {"n_total": 7, "n_judged": 2, "up": 1, "down": 1}, sel)
    assert "강남좋은아파트" not in title and "강남전세단지" not in title and "갭" not in title


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


def test_budget_links_open_exact_price_segment_and_keep_daily_attribution():
    digest = build_daily_digest(_sample_ds(), "2026-09-05", "2026-09-04")
    for surface, source in (("tistory_html", "tistory"), ("site_html", "owned_daily")):
        body = digest[surface]
        attribution = []
        for label in ("10억 미만", "10~15억", "15~20억", "20억 이상"):
            match = re.search(r'<a href="([^"]+)"[^>]*>' + re.escape(label) + r'</a>', body)
            assert match, f"{label} must be a real link"
            query = parse_qs(urlsplit(html_lib.unescape(match.group(1))).query)
            assert query["seg"] == [label]
            assert query["utm_source"] == [source]
            assert query["utm_medium"] == ["referral" if source == "tistory" else "internal"]
            attribution.append((query["utm_campaign"][0], query["utm_content"][0]))
        assert len(set(attribution)) == 1


def _area_row(gu="노원", name="평형관측단지", **changes):
    row = _row(gu, name, kapt_verified=True,
               kapt_area_units={"le60": 200, "60_85": 100, "85_135": 0, "gt135": 0},
               spread_flag="", med59_eok=8.0, med84_eok=10.0, n59=5, n84=7)
    row.update(changes)
    return row


def test_both_daily_surfaces_show_eligible_area_observations_with_source_and_samples():
    rows = [_area_row(), _area_row(name="신원불일치제외", spread_flag="ambiguous_name"),
            _area_row(name="작은표본제외", n84=4)]
    ds = {"complexes": rows, "count": 3, "data_asof": "2026-09-04"}
    digest = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    for surface in ("tistory_html", "site_html"):
        body = digest[surface]
        assert "59㎡·84㎡ 실거래 중위 관측" in body
        assert "평형관측단지" in body
        assert "8억" in body and "10억" in body
        assert "n=5" in body and "n=7" in body
        assert "국토부 수집 스냅샷 · 기준일 2026-09-04" in body
        assert "각 ±3.5㎡" in body
        assert "신원불일치제외" not in body
        assert "작은표본제외" not in body
    assert "<main class=wrap>" in digest["site_html"]
    assert "<article>" in digest["site_html"]
    assert '<section id="area-tracks"' in digest["site_html"]
    assert '<th scope="col">59㎡대 중위·n</th>' in digest["site_html"]


def test_tistory_partial_area_table_has_all_rows_on_owned_daily():
    rows = [_area_row(name=f"평형단지{i:02}", complex_no=str(i)) for i in range(8)]
    ds = {"complexes": rows, "count": len(rows), "data_asof": "2026-09-04"}
    digest = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    # 외부 리뷰 C15 — 가나다순 첫 N개 대신 84㎡ 중위 가격대마다 표본 많은 예시 1곳(이 표본은 모두 10~15억대).
    assert "기준 충족 8단지 중 84㎡ 중위 가격대별 두 평형 표본 최다 1곳" in digest["tistory_html"]
    assert "#area-tracks" in digest["tistory_html"]
    assert "평형단지07" not in digest["tistory_html"]
    assert "평형단지07" in digest["site_html"]


def test_tistory_budget_shrinks_only_optional_detail_rows_and_preserves_required_summary(monkeypatch):
    rows = [_area_row(gu=f"구{i:02}", name=f"평형단지{i:02}", complex_no=str(i),
                      molit_pos_52w=100, molit_trend_dir="▲", molit_trend_pct=5,
                      turnover_pct=15, jeonse_n=8, jeonse_ratio_complex_pct=80, gap_eok=2)
            for i in range(25)]
    ds = {"complexes": rows, "count": len(rows), "data_asof": "2026-09-04"}
    ds["listing_inventory"] = _inventory()
    full = build_daily_digest(ds, "2026-09-05", "2026-09-04")["tistory_html"]
    budget = 23000  # Preserve reader overview and all 25 default + 25 synthetic districts.
    assert len(full.encode("utf-8")) > budget
    monkeypatch.setattr(blog.daily_digest, "_TISTORY_TEXT_BUDGET", budget)
    digest = build_daily_digest(ds, "2026-09-05", "2026-09-04")
    body = digest["tistory_html"]
    assert len(body.encode("utf-8")) <= budget
    assert "아래 반복 상세 표는 각 최대" in body
    for required in ("30초 브리핑", "오늘의 숫자", "가격대별 요약", "서울 25개 구 요약", "국토부 RTMS",
                     "게이트:", "표본수", "방법론 전문", "59㎡·84㎡ 실거래 중위 관측"):
        assert required in body
    for i in range(25):
        assert f"<b>구{i:02}</b>" in body


def test_budget_compaction_keeps_macro_observations(monkeypatch):
    """Removing macro_html from any compaction pass loses a required reader section."""
    rows = [_area_row(gu=f"구{i:02}", name=f"평형단지{i:02}", complex_no=str(i),
                      molit_pos_52w=100, molit_trend_dir="▲", molit_trend_pct=5,
                      turnover_pct=15, jeonse_n=8, jeonse_ratio_complex_pct=80, gap_eok=2)
            for i in range(25)]
    ds = {"complexes": rows, "count": len(rows), "listing_inventory": _inventory()}
    macro = {"tistory_html": '<p><b>거시 지표</b> · 한국은행 기준금리 3.00% · 기준일 2026-09-05</p>',
             "site_html": '<h2>거시 지표</h2><p>한국은행 기준금리 3.00%</p>'}
    monkeypatch.setattr(blog.daily_digest, "_TISTORY_TEXT_BUDGET", 23000)
    body = build_daily_digest(ds, "2026-09-05", "2026-09-04", macro=macro)["tistory_html"]
    assert "아래 반복 상세 표는 각 최대" in body
    assert "한국은행 기준금리 3.00%" in body
    assert len(body.encode("utf-8")) <= 23000


def test_full_macro_and_five_housing_indicators_survive_large_digest():
    import json
    from pathlib import Path
    from agent_realestate.collectors.macro import build_indicator
    from blog.macro_context import build_macro_context
    snap = json.loads(Path("tests/fixtures/macro/snapshot_2026-09-07.json").read_text())
    for code, unit, value in (("kr_mortgage_new", "%", 4.0), ("seoul_apt_sale", "2026.06=100", 102),
                              ("seoul_apt_jeonse", "2026.06=100", 101), ("seoul_apt_sales", "호", 12345),
                              ("seoul_unsold", "호", 0)):
        snap["indicators"][code] = build_indicator(code, code, unit, "monthly", "공식 출처", "https://ecos.bok.or.kr/",
                                                [("2026-07-01", 100), ("2026-08-01", value)])
    rows = [_area_row(gu=f"구{i:02}", name=f"긴이름평형단지{i:02}", complex_no=str(i),
                      molit_pos_52w=100, turnover_pct=15, jeonse_n=8, jeonse_ratio_complex_pct=80, gap_eok=2)
            for i in range(25)]
    ds = {"complexes": rows, "count": 25, "listing_inventory": _inventory()}
    ctx = build_macro_context(snap, "2026-09-07")
    body = build_daily_digest(ds, "2026-09-07", "2026-09-06", macro=ctx)["tistory_html"]
    assert len(body.encode()) <= 28000
    for label in ("한국은행 기준금리", "신규취급액 코픽스", "국고채 3년", "주택담보대출 금리",
                  "서울 아파트 매매가격지수", "서울 아파트 전세가격지수", "서울 아파트 매매거래량", "서울 미분양 주택"):
        assert label in body
    assert "12,345호" in body and "0호" in body
    for i in range(25):
        assert f"<b>구{i:02}</b>" in body


@pytest.mark.parametrize("surface", ["tistory_html", "site_html"])
def test_mobile_daily_area_tables_and_price_links_fit_viewport(surface, required_browser):
    rows = [_area_row(name=f"평형관측긴단지이름{i}", complex_no=str(i),
                      turnover_pct=15-i, molit_pos_52w=i+1) for i in range(8)]
    digest = build_daily_digest({"complexes": rows, "count": len(rows)}, "2026-09-05", "2026-09-04")
    document = digest[surface]
    if surface == "tistory_html":
        document = ('<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8">'
                    '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
                    '<body style="margin:0;padding:14px;font:14px/1.5 sans-serif">' + document + '</body></html>')
    page = required_browser.new_page(viewport={"width": 390, "height": 844})
    try:
        page.route("**/*", lambda route: route.abort())
        page.set_content(document, wait_until="domcontentloaded")
        assert page.evaluate("document.documentElement.scrollWidth") <= 390
        if surface == "site_html":
            assert page.locator('[data-reader-overview]').is_visible()
            assert not page.locator('#reader-details').get_attribute('open')
            page.locator('#reader-details > summary').click()
        assert page.get_by_text("서울 아파트 매수를 검토할 때, 어떤 실거래 관측부터 확인하면 좋을까요?", exact=True).is_visible()
        assert page.get_by_text("12개월 회전율 상위 관측", exact=True).is_visible()
        assert page.get_by_text("52주 범위 하단 근접 관측", exact=True).is_visible()
        budget_links = page.locator('a[href*="explorer.html?seg="]')
        assert budget_links.count() == 4
        for link in budget_links.all():
            assert link.bounding_box()["height"] >= 44
        if surface == "site_html":
            assert page.locator("#area-tracks tbody tr").count() == 8
            assert page.locator("main article").count() == 1
    finally:
        page.close()


@pytest.mark.parametrize('surface', ['tistory_html', 'site_html'])
def test_daily_footer_discloses_ai_assistance_on_both_surfaces(surface):
    from html.parser import HTMLParser

    class VisibleText(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []

        def handle_data(self, data):
            self.text.append(data)

    body = build_daily_digest({'complexes': []}, '2026-10-04', '2026-10-04')[surface]
    parser = VisibleText()
    parser.feed(body)
    # 외부 리뷰 C16 — 생성일과 사람 검수 여부를 따로 적는다.
    assert ('공식 통계·공고를 AI로 정리했습니다(생성 2026-10-04·사람 검수 미실시). '
            '지원 조건은 원문에서 확인하세요.') in ''.join(parser.text)
