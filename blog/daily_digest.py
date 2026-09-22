"""일간 다이제스트(2026-09-05 P1) — 티스토리 1편 + 사이트 1페이지, 25구 통합 덤프 대체.
선택 로직(gate)은 report/blog-growth-2026-09-05/canvas/gen_digest.py 프로토타입을 그대로 계승:
아파트·전용 40㎡+·매매표본 10건+ 만 순위 대상(build_explorer.passes_rank_gate 등 단일소스).
A모델 무점수 — 정렬·상단/하단 근접은 관측된 사실 위치일 뿐 추천이 아니다(wording_guard 로 강제).
"""
from __future__ import annotations
import html
import json
from urllib.parse import quote

import blog.build_explorer as be
import blog.complex_page as cp
from blog.build_site import BASE_URL, ga4_snippet
from blog.fact_lead import build_fact_leads, render_lead_block, render_lead_lines
from blog.macro_entry import macro_entry_attributes
from blog.tistory_draft import _TBL, _TH, _TD, _MUT, TISTORY_TAGS
from blog.wording_guard import assert_wording_ok

_HI_POS = 99   # 12개월 범위 상단 근접 임계(52주 위치 %)
_LO_POS = 6    # 12개월 범위 하단 근접 임계

_SITE_CSS = (
    "*{box-sizing:border-box}body{margin:0;background:#f7f5f0;color:#1b1a17;"
    'font-family:"IBM Plex Sans KR",-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;'
    "font-size:14px;line-height:1.5;font-variant-numeric:tabular-nums}"
    "a{color:#1d6f6a;text-decoration:none}a:hover{text-decoration:underline}"
    "h1,h2,p{margin:0}"
    ".wrap{max-width:720px;margin:0 auto;padding:20px 20px 56px}"
    "nav.top{display:flex;gap:16px;font-size:13px;color:#5c584f;margin-bottom:16px;flex-wrap:wrap}"
    ".crumb{font-size:12px;color:#8a857a;margin-bottom:6px}"
    "h1{font-size:24px;font-weight:700;letter-spacing:-.02em;margin-bottom:4px}"
    "h2{font-size:16px;font-weight:700;margin:26px 0 8px}"
    ".meta{font-size:13px;color:#5c584f;margin-bottom:16px}"
    ".mut{color:#8a857a;font-size:12px}"
    ".up{color:#c43d2f}.down{color:#2f5fc4}"
    ".tiles{display:flex;gap:10px;flex-wrap:wrap}"
    ".tile{background:#fff;border:1px solid #e6e2d9;border-radius:10px;padding:12px 14px;flex:1 1 110px;min-width:100px}"
    ".tile .k{font-size:12px;color:#8a857a;display:block}.tile .v{font-size:19px;font-weight:700}"
    ".notice{background:#fff;border:1px solid #e6e2d9;border-radius:10px;padding:14px;color:#5c584f}"
    ".tblwrap{overflow-x:auto;background:#fff;border:1px solid #e6e2d9;border-radius:10px;margin-top:4px}"
    "table{width:100%;border-collapse:collapse;font-size:13px}"
    "th,td{padding:7px 9px;border-bottom:1px solid #e6e2d9;text-align:right;white-space:nowrap}"
    "th{color:#5c584f;font-weight:500;font-size:12px;border-bottom:1px solid #cfc9bc}"
    "td:first-child,th:first-child{text-align:left;white-space:normal}"
    "tbody tr:hover{background:#faf9f6}"
    ".foot{font-size:12px;color:#8a857a;margin-top:18px;line-height:1.6}"
    "@media(max-width:760px){.wrap{padding:14px 14px 40px}.tiles{gap:8px}"
    ".tile{flex:1 1 45%;padding:10px 12px}table{font-size:12px}th,td{padding:6px 7px}}"
    ".lead{background:#f7f5f0;color:#1b1a17;border:1px solid #e6e2d9;border-radius:10px;"
    "padding:14px 16px;margin-bottom:4px;font-size:15px;font-variant-numeric:tabular-nums}"
    ".lead h2{font-size:15px;font-weight:700;margin-bottom:8px;color:#1d6f6a}"
    ".lead ul{margin:0;padding-left:18px}.lead li{margin-bottom:6px;line-height:1.5}"
    ".lead-note{font-size:12px;color:#8a857a;margin-top:8px}"
)


def _eok(v: float | None) -> str:
    return f"{v:g}억" if v is not None else "—"


def _trend_txt(r: dict) -> str:
    d, p = r.get("molit_trend_dir"), r.get("molit_trend_pct")
    if p is None:
        return "—"
    return "보합" if d == "—" else f"{d}{abs(p):g}%"


def _pos_txt(r: dict) -> str:
    v = r.get("molit_pos_52w")
    return f"{v:g}%" if v is not None else "—"


def _hub_url_abs(r: dict) -> str:
    """티스토리(외부 호스트)용 — 항상 절대 URL."""
    return f'{BASE_URL}/gu/{quote(r["gu"])}.html#{be.slugify_complex_name(r["name"])}'


def _hub_url_rel(r: dict) -> str:
    """사이트 페이지({outdir}/daily/*.html → site/daily/*.html)용 — 1단계 상위 상대경로."""
    return f'../gu/{quote(r["gu"])}.html#{be.slugify_complex_name(r["name"])}'


def _complex_url_abs(r: dict) -> str:
    """단지 개별 페이지(2026-09-05 P2) 절대 URL — 티스토리(외부 호스트)용."""
    return f'{BASE_URL}/complex/{quote(cp.complex_slug(r["gu"], r["name"]))}.html'


def _complex_url_rel(r: dict) -> str:
    """단지 개별 페이지(2026-09-05 P2) 상대 URL — 사이트 daily/*.html 용."""
    return f'../complex/{quote(cp.complex_slug(r["gu"], r["name"]))}.html'


def _select_ranked(ds: dict) -> dict:
    cx = ds["complexes"]
    base = [r for r in cx if be.passes_rank_gate(r)]
    hi = sorted([r for r in base if (r.get("molit_pos_52w") or 0) >= _HI_POS],
                key=lambda r: (-(r["molit_pos_52w"]), -(r.get("molit_trend_pct") or 0)))[:4]
    lo = sorted([r for r in base if r.get("molit_pos_52w") is not None and r["molit_pos_52w"] <= _LO_POS],
                key=lambda r: r["molit_pos_52w"])[:4]
    jr = sorted([r for r in base if be.passes_jeonse_gate(r)],
                key=lambda r: -r["jeonse_ratio_complex_pct"])[:5]
    tv = sorted([r for r in base if be.passes_turnover_gate(r)],
                key=lambda r: -r["turnover_pct"])[:5]
    return {"hi": hi, "lo": lo, "jr": jr, "tv": tv}


def _gu_summary_rows(ds: dict) -> list[dict]:
    by: dict[str, list[dict]] = {}
    for r in ds["complexes"]:
        by.setdefault(r["gu"], []).append(r)
    return [{"gu": gu, "n": len(by[gu]), "gu_median": be.compute_gu_median(by[gu]),
             "up": sum(1 for r in by[gu] if r.get("molit_trend_dir") == "▲"),
             "down": sum(1 for r in by[gu] if r.get("molit_trend_dir") == "▼")}
            for gu in sorted(by)]


def _band_summary_rows(ds: dict) -> list[dict]:
    """가격대(PriceBand) 4밴드 요약(2026-09-07) — 단지 수(전체)·중위의 중위(게이트 통과)·52주 상단/하단 근접 수(게이트 통과).
    밴드는 12개월 중위에서 재계산(price_segment 필드와 동일 함수) — 필드 없는 입력(테스트·옛 스냅샷)도 같은 결과."""
    by: dict[str, list[dict]] = {b: [] for _, b in be.PRICE_SEGMENTS}
    for r in ds["complexes"]:
        b = be.price_segment(r.get("molit_recent_eok"))
        if b:
            by[b].append(r)
    return [{"band": b, "n": len(rows), "median": be.compute_gu_median(rows),
             "hi": sum(1 for r in rows if be.passes_rank_gate(r) and (r.get("molit_pos_52w") or 0) >= _HI_POS),
             "lo": sum(1 for r in rows if be.passes_rank_gate(r) and r.get("molit_pos_52w") is not None and r["molit_pos_52w"] <= _LO_POS)}
            for b, rows in by.items()]


def _today_counts(ds: dict) -> dict:
    cx = ds["complexes"]
    return {
        "n_total": ds.get("count", len(cx)),
        "n_sample": sum(r.get("molit_n") or 0 for r in cx),
        "up": sum(1 for r in cx if r.get("molit_trend_dir") == "▲"),
        "down": sum(1 for r in cx if r.get("molit_trend_dir") == "▼"),
        "flat": sum(1 for r in cx if r.get("molit_trend_dir") == "—"),
    }


def _delta_txt(value) -> str:
    return "—" if value is None else f"{value:+,}"


def _inventory_delta(inventory: dict, gu: str, period: str, metric: str = "total_article_count"):
    try:
        return inventory["comparisons"][period]["districts"][gu][metric]["delta"]
    except (KeyError, TypeError):
        return None


def _inventory_notice(inventory: dict | None) -> str:
    if not inventory:
        return "매물 재고 관측이 아직 없습니다."
    if inventory.get("status") == "missing":
        return inventory.get("unavailable_reason") or "매물 재고 관측이 아직 없습니다."
    if not inventory.get("complete"):
        return "25개 구 전체 수집을 완료하지 못해 부분 합계와 증감은 공개하지 않습니다."
    return "관측 후 36시간을 초과해 현재 합계와 증감은 공개하지 않습니다."


def _render_inventory_tistory(inventory: dict | None, today: str) -> str:
    if not inventory or not inventory.get("fresh"):
        return (f'<p><b>구별 네이버 표시 매물</b></p><p style="{_MUT}">'
                f'{html.escape(_inventory_notice(inventory))}</p>')
    total = inventory.get("total", {})
    districts = inventory.get("districts", {})
    period = "7d" if any(_inventory_delta(inventory, gu, "7d") is not None for gu in districts) else "1d"
    comparable = any(_inventory_delta(inventory, gu, period) is not None for gu in districts)
    rows = sorted(
        districts.items(),
        key=lambda item: (
            -(abs(_inventory_delta(inventory, item[0], period)) if comparable else item[1].get("total_article_count", 0)),
            item[0],
        ),
    )[:8]
    label = "7일 Δ" if period == "7d" else "1일 Δ"
    trs = "".join(
        f'<tr><td style="{_TD}"><b>{html.escape(gu)}</b></td>'
        f'<td style="{_TD}">{values.get("total_article_count", 0):,}</td>'
        f'<td style="{_TD}">{_delta_txt(_inventory_delta(inventory, gu, period))}</td>'
        f'<td style="{_TD}">{values.get("sale_article_count", 0):,}</td>'
        f'<td style="{_TD}">{values.get("lease_article_count", 0):,}</td></tr>'
        for gu, values in rows
    )
    baseline_note = "" if comparable else " 비교 가능한 같은 방식의 기준 관측은 아직 없습니다."
    observed = html.escape(str(inventory.get("observed_at") or ""))
    return (
        '<p><b>서울 25개 구 네이버 표시 매물</b></p>'
        f'<p>전체 {total.get("total_article_count", 0):,}건 · 매매 {total.get("sale_article_count", 0):,}건 · '
        f'전세 {total.get("lease_article_count", 0):,}건 · 월세 {total.get("rent_article_count", 0):,}건 · '
        f'단기 {total.get("short_term_rent_article_count", 0):,}건 · '
        f'물리 단지 {total.get("physical_complex_count", 0):,}곳 · 관측 {observed}.{baseline_note}</p>'
        f'<table style="{_TBL}"><tr><td style="{_TH}"><b>구</b></td><td style="{_TH}"><b>전체</b></td>'
        f'<td style="{_TH}"><b>{label}</b></td><td style="{_TH}"><b>매매</b></td>'
        f'<td style="{_TH}"><b>전세</b></td></tr>{trs}</table>'
        f'<p style="{_MUT}">절대 증감이 큰 8개 구만 표시합니다. 25개 구 전체 1일·7일 표는 '
        f'<a href="{BASE_URL}/daily/{today}.html">사이트 일간 페이지</a>에서 확인할 수 있습니다. '
        '네이버 법정동별 단지 목록의 표시 건수 합계이며 전체는 매매·전세·월세·단기임대를 더한 값입니다. '
        '한 주택 수나 수요를 뜻하지 않고 중개사 중복 노출이 있을 수 있습니다.</p>'
    )


def _render_inventory_site(inventory: dict | None) -> str:
    if not inventory or not inventory.get("fresh"):
        return (
            '<h2>구별 네이버 표시 매물</h2><div class="notice">'
            f'{html.escape(_inventory_notice(inventory))}</div>'
        )
    total = inventory.get("total", {})
    districts = inventory.get("districts", {})
    rows = "".join(
        f'<tr><td><a href="../gu/{quote(gu)}.html">{html.escape(gu)}</a></td>'
        f'<td>{values.get("total_article_count", 0):,}</td>'
        f'<td>{_delta_txt(_inventory_delta(inventory, gu, "1d"))}</td>'
        f'<td>{_delta_txt(_inventory_delta(inventory, gu, "7d"))}</td>'
        f'<td>{values.get("sale_article_count", 0):,}</td>'
        f'<td>{values.get("lease_article_count", 0):,}</td>'
        f'<td>{values.get("rent_article_count", 0):,}</td>'
        f'<td>{values.get("physical_complex_count", 0):,}</td></tr>'
        for gu, values in sorted(districts.items())
    )
    observed = html.escape(str(inventory.get("observed_at") or ""))
    return f"""
<h2>구별 네이버 표시 매물 <span class=mut>(25개 구 전체 아파트)</span></h2>
<div class=tiles>
<div class=tile><span class=k>전체 표시건수</span><span class=v>{total.get('total_article_count', 0):,}</span></div>
<div class=tile><span class=k>매매 표시건수</span><span class=v>{total.get('sale_article_count', 0):,}</span></div>
<div class=tile><span class=k>전세 표시건수</span><span class=v>{total.get('lease_article_count', 0):,}</span></div>
<div class=tile><span class=k>월세 표시건수</span><span class=v>{total.get('rent_article_count', 0):,}</span></div>
<div class=tile><span class=k>물리 단지</span><span class=v>{total.get('physical_complex_count', 0):,}</span></div>
<div class=tile><span class=k>매매 표시 단지</span><span class=v>{total.get('complexes_with_sale_articles', 0):,}</span></div>
</div>
<div class=tblwrap><table><tr><th>구</th><th>전체</th><th>1일 Δ</th><th>7일 Δ</th><th>매매</th><th>전세</th><th>월세</th><th>집계 단지</th></tr>{rows}</table></div>
<p class=mut style="margin-top:8px">관측 {observed}. 같은 출처·범위·방법의 정확히 1일·7일 전 완전 관측이 없으면 Δ는 —입니다. 네이버 법정동별 단지 목록의 표시 건수 합계이며 전체는 매매·전세·월세·단기임대를 더한 값입니다. 한 주택 수나 수요를 뜻하지 않고 중개사 중복 노출이 있을 수 있습니다.</p>
"""


def _render_tistory(today, asof, counts, sel, gu_rows, leads, band_rows=None, macro_html="", inventory=None) -> str:
    def name_cell(r):
        href = _complex_url_abs(r) if cp.passes_complex_page_gate(r) else _hub_url_abs(r)
        return (f'<a href="{href}"><b>{r["name"]}</b></a>({r["gu"]})'
                f'<br><span style="{_MUT}">{r["area_m2"]:g}㎡</span>')

    def table(headers, rows):
        head = "".join(f'<td style="{_TH}"><b>{h}</b></td>' for h in headers)
        return f'<table style="{_TBL}"><tr>{head}</tr>{"".join(rows)}</table>'

    hi_rows = [f'<tr><td style="{_TD}">{name_cell(r)}</td>'
               f'<td style="{_TD}">{_eok(r["molit_recent_eok"])} n{r["molit_n"]}</td>'
               f'<td style="{_TD}">{_trend_txt(r)}</td><td style="{_TD}">{_pos_txt(r)}</td></tr>'
               for r in sel["hi"]]
    lo_rows = [f'<tr><td style="{_TD}">{name_cell(r)}</td>'
               f'<td style="{_TD}">{_eok(r["molit_recent_eok"])} n{r["molit_n"]}</td>'
               f'<td style="{_TD}">{_trend_txt(r)}</td><td style="{_TD}">{_pos_txt(r)}</td></tr>'
               for r in sel["lo"]]
    jr_rows = [f'<tr><td style="{_TD}">{name_cell(r)}</td><td style="{_TD}">{_eok(r["molit_recent_eok"])}</td>'
               f'<td style="{_TD}">{r["jeonse_ratio_complex_pct"]:g}%</td>'
               f'<td style="{_TD}">{_eok(r.get("gap_eok"))}</td></tr>' for r in sel["jr"]]
    tv_rows = [f'<tr><td style="{_TD}">{name_cell(r)}</td><td style="{_TD}">{_eok(r["molit_recent_eok"])}</td>'
               f'<td style="{_TD}">{r["turnover_pct"]:g}%</td><td style="{_TD}">{r["molit_n"]}건</td></tr>'
               for r in sel["tv"]]
    gu_tr = "".join(
        f'<tr><td style="{_TD}"><a href="{BASE_URL}/gu/{quote(g["gu"])}.html"><b>{g["gu"]}</b></a></td>'
        f'<td style="{_TD}">{g["n"]}</td><td style="{_TD}">{_eok(g["gu_median"])}</td>'
        f'<td style="{_TD}">▲{g["up"]}·▼{g["down"]}</td></tr>' for g in gu_rows)

    none_p = f'<p style="{_MUT}">기준 충족 단지 없음</p>'
    lead_ps = [f"<p>{line}</p>" for line in render_lead_lines(leads)]
    band_tr = "".join(f'<tr><td style="{_TD}">{b["band"]}</td><td style="{_TD}">{b["n"]}</td>'
                      f'<td style="{_TD}">{_eok(b["median"])}</td><td style="{_TD}">▲{b["hi"]}·▼{b["lo"]}</td></tr>' for b in (band_rows or []))
    parts = [
        *lead_ps,
        *([macro_html] if macro_html else []),   # 거시 지표 스트립(2026-09-07 S2) — 컨텍스트 없으면 생략
        _render_inventory_tistory(inventory, today),
        f'<p><b>오늘의 숫자</b> — 기준일 {asof} · 발행 {counts["n_total"]}단지 · 표본 {counts["n_sample"]}건 · '
        f'상승 {counts["up"]} · 하락 {counts["down"]} · 보합 {counts["flat"]}(국토부 실거래 사실, 자체 점수 없음)</p>',
        *([ '<p><b>가격대별 요약</b>(12개월 중위 구간·사실)</p>',
            f'<table style="{_TBL}"><tr><td style="{_TH}"><b>가격대</b></td><td style="{_TH}"><b>단지 수</b></td>'
            f'<td style="{_TH}"><b>중위(억)</b></td><td style="{_TH}"><b>52주 상단·하단</b></td></tr>{band_tr}</table>'] if band_rows else []),
        '<p><b>12개월 범위 상단 근접</b>(52주 위치 99% 이상)</p>',
        table(["단지(구)", "중위(억) n", "3/9개월", "52주 위치"], hi_rows) if hi_rows else none_p,
        '<p><b>12개월 범위 하단 근접</b>(52주 위치 6% 이하)</p>',
        table(["단지(구)", "중위(억) n", "3/9개월", "52주 위치"], lo_rows) if lo_rows else none_p,
        '<p><b>전세가율 상위 5</b></p>',
        table(["단지(구)", "중위(억)", "전세가율", "매매-전세 갭"], jr_rows) if jr_rows else none_p,
        '<p><b>회전율 상위 5</b></p>',
        table(["단지(구)", "중위(억)", "회전율", "12개월 거래"], tv_rows) if tv_rows else none_p,
        '<p><b>구별 요약(25개 구)</b></p>',
        f'<table style="{_TBL}"><tr><td style="{_TH}"><b>구</b></td><td style="{_TH}"><b>단지 수</b></td>'
        f'<td style="{_TH}"><b>구 중위(억)</b></td><td style="{_TH}"><b>추세</b></td></tr>{gu_tr}</table>',
        f'<p style="{_MUT}">게이트: 상단/하단·전세가율·회전율 표는 아파트·전용 40㎡ 이상·매매표본 10건 이상만 대상'
        f'(전세가율은 추가로 전세표본 5건 이상·95% 이하, 회전율은 회전율 값 존재). '
        f'3/9개월=최근 3개월 중위 vs 직전 9개월 중위(과거 비교 사실, 전망 아님 — ▲/▼ 옆 %는 "직전 9개월보다 이만큼 높음/낮음"). '
        f'52주 위치=최근 3개월 체결 중위의 12개월 실거래 최저~최고 레인지 내 위치(%, 예: 74%면 1년 범위에서 74% 지점). '
        f'회전율=12개월 거래건수÷세대수×100(%). 구 중위=게이트 통과 단지 중위의 중위(억). '
        f'기준일 {asof}, 표본수는 각 셀 n 표기. 가격·거래는 국토부 RTMS 공공데이터, '
        f'매물 노출 건수는 별도 관측시각의 네이버 법정동별 단지 목록 집계입니다.</p>',
        f'<p style="{_MUT}">{be.DISCLAIMER} {be._takedown()}</p>',
        f'<p><a href="{BASE_URL}/">전체 탐색기·인덱스</a> · <a href="{BASE_URL}/methodology.html">방법론 전문</a></p>',
    ]
    return "".join(parts)


def _render_site(today, asof, counts, sel, gu_rows, title, leads, band_rows=None, macro_html="", inventory=None) -> str:
    def name_cell(r):
        href = _complex_url_rel(r) if cp.passes_complex_page_gate(r) else _hub_url_rel(r)
        return (f'<a href="{href}"><b>{r["name"]}</b></a> <span class=mut>({r["gu"]})</span>'
                f'<br><span class=mut>{r["area_m2"]:g}㎡</span>')

    def table(headers, rows):
        head = "".join(f"<th>{h}</th>" for h in headers)
        return f"<table><tr>{head}</tr>{''.join(rows)}</table>"

    hi_rows = [f'<tr><td>{name_cell(r)}</td><td>{_eok(r["molit_recent_eok"])} <span class=mut>n{r["molit_n"]}</span></td>'
               f'<td>{_trend_txt(r)}</td><td>{_pos_txt(r)}</td></tr>' for r in sel["hi"]]
    lo_rows = [f'<tr><td>{name_cell(r)}</td><td>{_eok(r["molit_recent_eok"])} <span class=mut>n{r["molit_n"]}</span></td>'
               f'<td>{_trend_txt(r)}</td><td>{_pos_txt(r)}</td></tr>' for r in sel["lo"]]
    jr_rows = [f'<tr><td>{name_cell(r)}</td><td>{_eok(r["molit_recent_eok"])}</td>'
               f'<td>{r["jeonse_ratio_complex_pct"]:g}%</td><td>{_eok(r.get("gap_eok"))}</td></tr>' for r in sel["jr"]]
    tv_rows = [f'<tr><td>{name_cell(r)}</td><td>{_eok(r["molit_recent_eok"])}</td>'
               f'<td>{r["turnover_pct"]:g}%</td><td>{r["molit_n"]}건</td></tr>' for r in sel["tv"]]
    gu_tr = "".join(f'<tr><td><a href="../gu/{quote(g["gu"])}.html">{g["gu"]}</a></td><td>{g["n"]}</td>'
                    f'<td>{_eok(g["gu_median"])}</td><td><span class=up>▲{g["up"]}</span>·'
                    f'<span class=down>▼{g["down"]}</span></td></tr>' for g in gu_rows)

    none_p = '<p class=mut>기준 충족 단지 없음</p>'
    band_tr = "".join(f'<tr><td>{b["band"]}</td><td>{b["n"]}</td><td>{_eok(b["median"])}</td>'
                      f'<td><span class=up>▲{b["hi"]}</span>·<span class=down>▼{b["lo"]}</span></td></tr>' for b in (band_rows or []))
    band_sec = (f'<h2>가격대별 요약 <span class=mut>(12개월 중위 구간·사실)</span></h2>'
                f'<div class=tblwrap><table><tr><th>가격대</th><th>단지 수</th><th>중위(억)</th><th>52주 상단·하단</th></tr>{band_tr}</table></div>') if band_rows else ''
    jsonld_bc = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "서울 부동산 데이터 스냅샷", "item": f"{BASE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": "오늘의 변화", "item": f"{BASE_URL}/daily/{today}.html"},
        ]}
    jsonld_ds = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"서울 아파트 오늘의 변화 {today}", "dateModified": today, "datePublished": today,
        "description": f"발행 {counts['n_total']}단지 국토부 공공 실거래 12개월 범위 상단/하단 근접·전세가율·회전율 사실 요약.",
        "license": "https://creativecommons.org/licenses/by-nc/4.0/",
        "creator": {"@type": "Organization", "name": "agent_realestate (개인 연구)"},
        "isAccessibleForFree": True, "keywords": ["부동산", "실거래", "공공데이터", "서울", "오늘의변화"]}

    return f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name=description content="서울 아파트 {counts['n_total']}단지 국토부 공공 실거래 오늘의 변화 — {today} 기준. 자체 점수·순위 없음, 투자자문 아님.">
<script type="application/ld+json">{json.dumps(jsonld_bc, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps(jsonld_ds, ensure_ascii=False)}</script>
<style>{_SITE_CSS}</style>
{ga4_snippet()}
</head><body>
<div class=wrap>
<nav class=top><a href="../index.html">구 허브</a><a href="../explorer.html">탐색기</a>
<a href="../daily/latest.html">오늘의 변화</a><a href="../macro.html" {macro_entry_attributes("daily", "macro")}>거시 지표</a><a href="../methodology.html">방법론</a></nav>
<div class=crumb><a href="../index.html">서울</a> › 오늘의 변화</div>
<h1>서울 아파트 오늘의 변화</h1>
<p class=meta>기준일 {asof} · 발행 {counts['n_total']}단지 · 표본 {counts['n_sample']}건</p>
{render_lead_block(leads)}
{macro_html}
{_render_inventory_site(inventory)}
<h2>오늘의 숫자</h2>
<div class=tiles>
<div class=tile><span class=k>발행 단지</span><span class=v>{counts['n_total']}</span></div>
<div class=tile><span class=k>표본 합계</span><span class=v>{counts['n_sample']:,}</span></div>
<div class=tile><span class=k>상승</span><span class="v up">▲{counts['up']}</span></div>
<div class=tile><span class=k>하락</span><span class="v down">▼{counts['down']}</span></div>
<div class=tile><span class=k>보합</span><span class=v>—{counts['flat']}</span></div>
</div>
{band_sec}
<h2>12개월 범위 상단 근접 <span class=mut>(52주 위치 99% 이상)</span></h2>
<div class=tblwrap>{table(["단지(구)", "중위(억) n", "3/9개월", "52주 위치"], hi_rows) if hi_rows else none_p}</div>

<h2>12개월 범위 하단 근접 <span class=mut>(52주 위치 6% 이하)</span></h2>
<div class=tblwrap>{table(["단지(구)", "중위(억) n", "3/9개월", "52주 위치"], lo_rows) if lo_rows else none_p}</div>

<h2>전세가율 상위 5</h2>
<div class=tblwrap>{table(["단지(구)", "중위(억)", "전세가율", "매매-전세 갭"], jr_rows) if jr_rows else none_p}</div>

<h2>회전율 상위 5</h2>
<div class=tblwrap>{table(["단지(구)", "중위(억)", "회전율", "12개월 거래"], tv_rows) if tv_rows else none_p}</div>

<h2>구별 요약 (25개 구)</h2>
<div class=tblwrap><table><tr><th>구</th><th>단지 수</th><th>구 중위(억)</th><th>추세</th></tr>{gu_tr}</table></div>

<div class=foot>
게이트: 상단/하단·전세가율·회전율 표는 아파트·전용 40㎡ 이상·매매표본 10건 이상만 대상(전세가율은 추가로 전세표본 5건 이상·95% 이하,
회전율은 회전율 값 존재). 3/9개월=최근 3개월 중위 vs 직전 9개월 중위(과거 비교 사실, 전망 아님 — ▲/▼ 옆 %는 "직전 9개월보다 이만큼 높음/낮음").
52주 위치=최근 3개월 체결 중위의 12개월 실거래 최저~최고 레인지 내 위치(%, 예: 74%면 1년 범위에서 74% 지점). 회전율=12개월 거래건수÷세대수×100(%).
구 중위=게이트 통과 단지 중위의 중위(억).
기준일 {asof}. 가격·거래는 국토부 RTMS 공공데이터, 매물 노출 건수는 별도 관측시각의 네이버 법정동별 단지 목록 집계.<br>
{be.DISCLAIMER} {be._takedown()}<br>
<a href="../methodology.html">방법론 전문</a> · <a href="../explorer.html">탐색기</a> ·
코드: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a>
</div>
</div>
</body></html>"""


def build_daily_digest(ds: dict, today: str, asof: str, prev_ds: dict | None = None, macro: dict | None = None) -> dict:
    """다이제스트 산출 — {"title","tags","tistory_html","site_html","summary"}.
    tistory_html 은 30,000바이트 예산을 넘거나 금칙어가 섞이면 ValueError 로 발행을 막는다.
    prev_ds(선택, 2026-09-06) = 직전 스냅샷 — 사실 리드(FactLead)의 패턴 재현 판정(§7 D)에만 쓰인다.
    macro(선택, 2026-09-07) = blog.macro_context.build_macro_context 결과 — 리드 다음에 거시 지표 스트립을 넣는다. None 이면 생략."""
    sel = _select_ranked(ds)
    gu_rows = _gu_summary_rows(ds)
    counts = _today_counts(ds)
    n_hi, n_lo = len(sel["hi"]), len(sel["lo"])
    leads = build_fact_leads(ds, "seoul", prev_ds=prev_ds)

    title = f"서울 아파트 오늘의 변화 — {today} · 12개월 범위 상단 {n_hi}곳·하단 {n_lo}곳 · {counts['n_total']}단지"
    tags = TISTORY_TAGS + ",오늘의변화"
    summary = (f"{today} 기준 {counts['n_total']}단지 · 표본 {counts['n_sample']}건 · "
               f"상승 {counts['up']}·하락 {counts['down']}·보합 {counts['flat']} · "
               f"12개월 범위 상단 근접 {n_hi}곳·하단 근접 {n_lo}곳")

    band_rows = _band_summary_rows(ds)   # 가격대 4밴드 요약(2026-09-07)
    inventory = ds.get("listing_inventory")
    tistory_html = _render_tistory(today, asof, counts, sel, gu_rows, leads, band_rows=band_rows,
                                    macro_html=(macro or {}).get("tistory_html", ""), inventory=inventory)
    site_html = _render_site(today, asof, counts, sel, gu_rows, title, leads, band_rows=band_rows,
                             macro_html=(macro or {}).get("site_html", ""), inventory=inventory)

    assert_wording_ok(tistory_html, "daily_digest:tistory_html")
    assert_wording_ok(site_html, "daily_digest:site_html")

    tb = len(tistory_html.encode("utf-8"))
    if tb > 30000 and macro:                                   # 거시 스트립은 선택 섹션(2026-09-07) — 예산 초과면 먼저 뺀다(사이트는 유지)
        tistory_html = _render_tistory(today, asof, counts, sel, gu_rows, leads, band_rows=band_rows,
                                       inventory=inventory)
        tb = len(tistory_html.encode("utf-8"))
    if tb > 30000:
        raise ValueError(f"[daily_digest] tistory_html {tb}B > 30000B 예산 초과 — 섹션을 줄이세요")

    return {"title": title, "tags": tags, "tistory_html": tistory_html,
            "site_html": site_html, "summary": summary}
