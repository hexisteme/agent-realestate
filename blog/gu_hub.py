"""자치구 허브 페이지(2026-09-05 P1) — 구 1개 = 감시 단지 전수 표 + 구 집계 사실.
A모델 무점수: '중위 desc' 정렬은 사실 정렬일 뿐 순위·추천이 아니다(방법론 각주 동봉).
구 중위·P25-P75·전세가율 중위는 passes_rank_gate/passes_jeonse_gate 통과분만 집계해
다이제스트(daily_digest.py)의 구별 요약과 동일 표본으로 맞춘다(단일소스: build_explorer 게이트).
"""
from __future__ import annotations
import html
from blog.brand_identity import AUTHOR_LABEL, BRAND_NAME, creator_schema
from urllib.parse import quote, unquote

import blog.build_explorer as be
import blog.complex_page as cp
from blog.community_participation import render_gu_panel
from blog.fact_lead import build_fact_leads, render_lead_block
from blog.wording_guard import assert_wording_ok

_CSS = ("*{box-sizing:border-box}body{margin:0;background:#f7f5f0;color:#1b1a17;"
        'font-family:"IBM Plex Sans KR",-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;'
        "font-size:14px;line-height:1.5;font-variant-numeric:tabular-nums}"
        "a{color:#1d6f6a;text-decoration:none}a:hover{text-decoration:underline}"
        "h1,h2,p{margin:0}"
        ".wrap{max-width:1180px;margin:0 auto;padding:20px 20px 56px}"
        "nav.top{display:flex;gap:16px;font-size:13px;color:#5c584f;margin-bottom:16px;flex-wrap:wrap}"
        ".crumb{font-size:12px;color:#8a857a;margin-bottom:6px}"
        "h1{font-size:26px;font-weight:700;letter-spacing:-.02em;margin-bottom:4px}"
        ".meta{font-size:13px;color:#5c584f;margin-bottom:18px}"
        ".tiles{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}"
        ".tile{background:#fff;border:1px solid #e6e2d9;border-radius:10px;padding:14px 16px;"
        "flex:1 1 150px;min-width:140px}"
        ".tile .k{font-size:12px;color:#8a857a}.tile .v{font-size:20px;font-weight:700;margin-top:2px}"
        ".panel{background:#fff;border:1px solid #e6e2d9;border-radius:10px;padding:14px 16px;margin-bottom:16px}"
        ".panel h2{font-size:15px;margin:0 0 8px}.panel p{font-size:12px;color:#5c584f;margin:3px 0}"
        ".up{color:#c43d2f}.down{color:#2f5fc4}"
        ".tblwrap{overflow-x:auto;background:#fff;border:1px solid #e6e2d9;border-radius:10px}"
        "table{width:100%;border-collapse:collapse;font-size:13px}"
        "th,td{padding:8px 9px;border-bottom:1px solid #e6e2d9;text-align:right;white-space:nowrap}"
        "th{color:#5c584f;font-weight:500;font-size:12px;border-bottom:1px solid #cfc9bc}"
        "td:first-child,th:first-child{text-align:left}"
        "tbody tr:hover{background:#faf9f6}"
        ".foot{font-size:12px;color:#8a857a;margin-top:16px;line-height:1.6}"
        "@media(max-width:760px){.wrap{padding:14px 14px 40px}.tiles{gap:8px}"
        ".tile{flex:1 1 45%;padding:10px 12px}.tile .v{font-size:17px}"
        "table{font-size:12px}th,td{padding:6px 7px}}"
        ".lead{background:#f7f5f0;color:#1b1a17;border:1px solid #e6e2d9;border-radius:10px;"
        "padding:14px 16px;margin-bottom:18px;font-size:15px;font-variant-numeric:tabular-nums}"
        ".lead h2{font-size:15px;font-weight:700;margin-bottom:8px;color:#1d6f6a}"
        ".lead ul{margin:0;padding-left:18px}.lead li{margin-bottom:6px;line-height:1.5}"
        ".lead-note{font-size:12px;color:#8a857a;margin-top:8px}")


def _eok(v: float | None) -> str:
    return f"{v:g}억" if v is not None else "—"


def _pct(v: float | None) -> str:
    return f"{v:g}%" if v is not None else "—"


def _pct_signed(v: float | None) -> str:
    if v is None:
        return "—"
    cls = "up" if v > 0 else ("down" if v < 0 else "")
    return f'<span class="{cls}">{v:+.1f}%</span>' if cls else f"{v:+.1f}%"


def _trend_cell(r: dict) -> str:
    d, p = r.get("molit_trend_dir"), r.get("molit_trend_pct")
    if p is None:
        return "—"
    if d == "—":
        return "보합"
    cls = "up" if d == "▲" else "down"
    return f'<span class="{cls}">{d}{abs(p):g}%</span>'


def _inventory_delta(inventory: dict, gu: str, period: str):
    try:
        return inventory["comparisons"][period]["districts"][gu]["total_article_count"]["delta"]
    except (KeyError, TypeError):
        return None


def _delta_text(value) -> str:
    return "—" if value is None else f"{value:+,}"


def _context_coverage(rows: list[dict], section: str) -> int:
    return sum(
        1 for row in rows
        if isinstance(row.get("living_context"), dict)
        and isinstance(row["living_context"].get(section), dict)
        and row["living_context"][section].get("status") != "missing"
    )


def _inventory_panel(gu: str, rows: list[dict], ds: dict | None) -> str:
    inventory = ds.get("listing_inventory") if isinstance(ds, dict) else None
    coverage = (
        f'생활정보 연결: 학군 {_context_coverage(rows, "school")}/{len(rows)} · '
        f'경사 {_context_coverage(rows, "terrain")}/{len(rows)} · '
        f'외부 후기 {_context_coverage(rows, "reviews")}/{len(rows)}단지'
    )
    if not isinstance(inventory, dict) or not inventory.get("fresh"):
        if isinstance(inventory, dict) and not inventory.get("complete"):
            message = "25개 구 전체 수집을 완료하지 못해 부분 매물 합계는 숨겼습니다."
        elif isinstance(inventory, dict) and inventory.get("status") == "stale":
            message = "매물 관측 후 36시간을 초과해 현재 합계와 증감은 숨겼습니다."
        else:
            message = "비교 가능한 매물 재고 관측이 아직 없습니다."
        return f'<div class="panel"><h2>네이버 표시 매물</h2><p>{message}</p><p>{coverage}</p></div>'
    values = inventory.get("districts", {}).get(gu)
    if not isinstance(values, dict):
        return f'<div class="panel"><h2>네이버 표시 매물</h2><p>이 구의 완전한 집계가 없습니다.</p><p>{coverage}</p></div>'
    observed = str(inventory.get("observed_at") or "")
    return (
        '<div class="panel"><h2>네이버 표시 매물 <span style="color:#8a857a;font-weight:400">'
        f'관측 {observed}</span></h2>'
        f'<p><b>전체 {values.get("total_article_count", 0):,}건</b> '
        f'(1일 {_delta_text(_inventory_delta(inventory, gu, "1d"))} · '
        f'7일 {_delta_text(_inventory_delta(inventory, gu, "7d"))}) · '
        f'매매 {values.get("sale_article_count", 0):,}건 · '
        f'전세 {values.get("lease_article_count", 0):,}건 · 월세 {values.get("rent_article_count", 0):,}건 · '
        f'단기 {values.get("short_term_rent_article_count", 0):,}건 · '
        f'집계 단지 {values.get("physical_complex_count", 0):,}곳</p>'
        '<p>네이버 법정동별 단지 목록의 표시 건수 합계입니다. 전체는 매매·전세·월세·단기임대를 더한 값이며, 한 주택 수나 수요를 뜻하지 않고 중개사 중복 노출이 있을 수 있습니다.</p>'
        f'<p>{coverage}</p></div>'
    )


def render_gu_hub(gu: str, rows: list[dict], asof: str, today: str,
                   ds: dict | None = None, prev_ds: dict | None = None,
                   weekly_post_href: str | None = None) -> str:
    """구 1개 허브 페이지(전체 HTML). rows = 그 구의 dataset complexes(전 유형 포함).
    ds(선택, 2026-09-06) = 전체 dataset — 주면 사실 리드(FactLead)가 서울 참조가 필요한 패밀리까지
    계산한다. 생략(None) 시 rows 만으로 합성 ds 를 만들어 구 내부 패밀리만 계산(서울 참조 패밀리는
    자동 스킵 — build_fact_leads 가 '다른 구 표본이 없다'는 사실로 판정, 별도 플래그 불필요)."""
    from blog.build_site import BASE_URL, ga4_snippet  # lazy: build_site 가 본 모듈을 import(순환 예방)
    from blog.search_intent import canonical_tag, district_intent

    intent = district_intent(gu, len(rows), today)

    lead_html = render_lead_block(build_fact_leads(ds or {"complexes": rows}, "gu", gu, prev_ds=prev_ds))
    # 주간 리포트 링크는 호출측이 실존 파일을 줄 때만(2026-09-06 P0 — 구별 일간 포스트 중단으로 ../posts/{today}-{gu}.html 은 비월요일 404).
    weekly_link = f'<a href="{weekly_post_href}">{gu} 최신 주간 리포트</a> · ' if weekly_post_href else ""
    n = len(rows)
    gated_vals = be.select_gated_medians(rows)
    gu_med = be.compute_gu_median(rows)
    p25 = round(be._pctile(gated_vals, 0.25), 2) if gated_vals else None
    p75 = round(be._pctile(gated_vals, 0.75), 2) if gated_vals else None
    up = sum(1 for r in rows if r.get("molit_trend_dir") == "▲")
    down = sum(1 for r in rows if r.get("molit_trend_dir") == "▼")
    flat = sum(1 for r in rows if r.get("molit_trend_dir") == "—")
    jeonse_med = be.compute_gu_jeonse_ratio_median(rows)
    inventory_panel = _inventory_panel(gu, rows, ds)
    community_panel = render_gu_panel(gu)

    srt = sorted(rows, key=lambda r: (r.get("molit_recent_eok") is None, -(r.get("molit_recent_eok") or 0), r["name"]))
    trs = []
    for r in srt:
        slug = html.escape(unquote(be.slugify_complex_name(r["name"])), quote=True)
        cmp_pct = (round((r["molit_recent_eok"] / gu_med - 1) * 100, 1)
                   if (r.get("molit_recent_eok") is not None and gu_med) else None)
        iqr = (f'{r["molit_p25_eok"]:g}–{r["molit_p75_eok"]:g}억'
               if (r.get("molit_p25_eok") is not None and r.get("molit_p75_eok") is not None) else "—")
        jeonse_cell = f'{r["jeonse_ratio_complex_pct"]:g}%' if be.passes_jeonse_gate(r) else "—"
        turnover_cell = f'{r["turnover_pct"]:g}%' if be.passes_turnover_gate(r) else "—"
        # 단지 개별 페이지 게이트 통과 시 그 페이지로 링크(2026-09-05 P2) — 미통과면 기존처럼 굵은 텍스트만(행 자체가 앵커).
        name_html = (f'<a href="../complex/{quote(cp.complex_slug(gu, r["name"]))}.html"><b>{r["name"]}</b></a>'
                     if cp.passes_complex_page_gate(r) else f'<b>{r["name"]}</b>')
        trs.append(
            f'<tr id="{slug}"><td>{name_html} <span style="color:#8a857a;font-size:12px">'
            f'{r.get("saeng") or ""}</span></td>'
            f'<td>{r["area_m2"]:g}㎡</td>'
            f'<td>{_eok(r.get("molit_recent_eok"))} <span style="color:#8a857a">n{r.get("molit_n") or 0}</span></td>'
            f'<td>{iqr}</td><td>{_pct_signed(cmp_pct)}</td><td>{_trend_cell(r)}</td>'
            f'<td>{_pct(r.get("molit_pos_52w"))}</td><td>{jeonse_cell}</td><td>{turnover_cell}</td></tr>'
        )

    tiles = (
        f'<div class=tile><span class=k>단지 수</span><span class=v>{n}</span></div>'
        f'<div class=tile><span class=k>구 중위(중위의 중위)</span><span class=v>{_eok(gu_med)}</span></div>'
        f'<div class=tile><span class=k>P25–P75(거래의 가운데 절반)</span>'
        f'<span class=v style="font-size:16px">{_eok(p25)}–{_eok(p75)}</span></div>'
        f'<div class=tile><span class=k>추세(3/9개월)</span>'
        f'<span class=v style="font-size:16px"><span class=up>▲{up}</span> · <span class=down>▼{down}</span> · —{flat}</span></div>'
        f'<div class=tile><span class=k>전세가율 중위(게이트)</span><span class=v>{_pct(jeonse_med)}</span></div>'
    )

    breadcrumb_ld = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": BRAND_NAME, "item": f"{BASE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": f"{gu}", "item": f"{BASE_URL}/gu/{quote(gu)}.html"},
        ]}
    dataset_ld = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"서울 {gu} 아파트 공공 실거래 구허브 {today}",
        "description": f"{gu} 감시 단지 {n}개의 국토부 공공 실거래 중위·분포·추세 구 단위 집계.",
        "dateModified": today, "license": "https://creativecommons.org/licenses/by-nc/4.0/",
        "creator": creator_schema(),
        "isAccessibleForFree": True, "keywords": ["부동산", "실거래", "공공데이터", "서울", gu]}

    import json as _json
    out = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{html.escape(intent.title)}</title>
<meta name=description content="{html.escape(intent.description, quote=True)}">
{canonical_tag(BASE_URL, intent)}
<script type="application/ld+json">{_json.dumps(breadcrumb_ld, ensure_ascii=False)}</script>
<script type="application/ld+json">{_json.dumps(dataset_ld, ensure_ascii=False)}</script>
<style>{_CSS}</style>
{ga4_snippet()}
</head><body>
<main class=wrap id=main-content><article>
<nav class=top><a href="../index.html">구 허브</a><a href="../explorer.html">탐색기</a>
<a href="../daily/latest.html">오늘의 변화</a><a href="../methodology.html">방법론</a></nav>
<div class=crumb><a href="../index.html">서울</a> › {gu}</div>
<h1>{gu} 아파트 실거래</h1>
<p class=meta>{n}단지 · 기준 {asof} · 국토부 실거래(신고 지연 최대 30일) · 매일 자동 갱신</p>
{lead_html}
{inventory_panel}
{community_panel}
<div class=tiles>{tiles}</div>
<div class=tblwrap><table><thead><tr>
<th>단지</th><th>전용</th><th>중위 n</th><th>P25–P75</th><th>구중위대비%</th>
<th>3/9개월</th><th>52주 위치</th><th>전세가율</th><th>회전율</th>
</tr></thead><tbody>{"".join(trs)}</tbody></table></div>
<div class=foot>
산식: 구 중위=아파트·전용40㎡+·매매표본10건+ 단지만 골라 그 단지들 중위의 중위(억). 구중위대비%=(단지 중위÷구 중위−1)×100.
3/9개월=최근 3개월 중위 vs 직전 9개월 중위(과거 비교 사실, 전망 아님 — ▲/▼ 옆 %는 "직전 9개월보다 이만큼 높음/낮음"). 52주 위치=최근 3개월 체결 중위가
12개월 실거래 최저~최고 레인지에서 차지하는 위치(%, 예: 74%면 1년 범위에서 74% 지점). 전세가율=전세 중위÷매매 중위(전세표본 5건 미만 또는 95% 초과·매매표본
10건 미만·비아파트·40㎡ 미만은 —). 회전율=12개월 거래건수÷세대수×100(%). 기준일 {asof}. 가격·거래는 국토부 RTMS 공공데이터,
매물 노출 건수는 별도 관측시각의 네이버 법정동별 단지 목록 집계이며 생활 맥락은 단지 페이지의 출처·확인 상태를 따름.<br>
{be.DISCLAIMER} {be._takedown()}<br>
작성 주체: {AUTHOR_LABEL} · 페이지 갱신 {today}<br>
<a href="../methodology.html">방법론 전문</a> · {weekly_link}
코드: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a>
</div>
</article></main>
</body></html>"""
    assert_wording_ok(out, f"gu_hub:{gu}")
    return out
