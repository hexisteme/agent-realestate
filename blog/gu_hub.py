"""자치구 허브 페이지(2026-09-05 P1) — 구 1개 = 감시 단지 전수 표 + 구 집계 사실.
A모델 무점수: '중위 desc' 정렬은 사실 정렬일 뿐 순위·추천이 아니다(방법론 각주 동봉).
구 중위·P25-P75·전세가율 중위는 passes_rank_gate/passes_jeonse_gate 통과분만 집계해
다이제스트(daily_digest.py)의 구별 요약과 동일 표본으로 맞춘다(단일소스: build_explorer 게이트).
"""
from __future__ import annotations
from urllib.parse import quote

import blog.build_explorer as be
import blog.complex_page as cp
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
        "table{font-size:12px}th,td{padding:6px 7px}}")


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


def render_gu_hub(gu: str, rows: list[dict], asof: str, today: str) -> str:
    """구 1개 허브 페이지(전체 HTML). rows = 그 구의 dataset complexes(전 유형 포함)."""
    from blog.build_site import BASE_URL, ga4_snippet  # lazy: build_site 가 본 모듈을 import(순환 예방)

    n = len(rows)
    gated_vals = be.select_gated_medians(rows)
    gu_med = be.compute_gu_median(rows)
    p25 = round(be._pctile(gated_vals, 0.25), 2) if gated_vals else None
    p75 = round(be._pctile(gated_vals, 0.75), 2) if gated_vals else None
    up = sum(1 for r in rows if r.get("molit_trend_dir") == "▲")
    down = sum(1 for r in rows if r.get("molit_trend_dir") == "▼")
    flat = sum(1 for r in rows if r.get("molit_trend_dir") == "—")
    jeonse_med = be.compute_gu_jeonse_ratio_median(rows)

    srt = sorted(rows, key=lambda r: (r.get("molit_recent_eok") is None, -(r.get("molit_recent_eok") or 0), r["name"]))
    trs = []
    for r in srt:
        slug = be.slugify_complex_name(r["name"])
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
        f'<div class=tile><span class=k>P25–P75(단지 중위 분포)</span>'
        f'<span class=v style="font-size:16px">{_eok(p25)}–{_eok(p75)}</span></div>'
        f'<div class=tile><span class=k>추세(3/9개월)</span>'
        f'<span class=v style="font-size:16px"><span class=up>▲{up}</span> · <span class=down>▼{down}</span> · —{flat}</span></div>'
        f'<div class=tile><span class=k>전세가율 중위(게이트)</span><span class=v>{_pct(jeonse_med)}</span></div>'
    )

    breadcrumb_ld = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "서울 부동산 데이터 스냅샷", "item": f"{BASE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": f"{gu}", "item": f"{BASE_URL}/gu/{quote(gu)}.html"},
        ]}
    dataset_ld = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"서울 {gu} 아파트 공공 실거래 구허브 {today}",
        "description": f"{gu} 감시 단지 {n}개의 국토부 공공 실거래 중위·분포·추세 구 단위 집계.",
        "dateModified": today, "license": "https://creativecommons.org/licenses/by-nc/4.0/",
        "creator": {"@type": "Organization", "name": "agent_realestate (개인 연구)"},
        "isAccessibleForFree": True, "keywords": ["부동산", "실거래", "공공데이터", "서울", gu]}

    import json as _json
    out = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{gu} 아파트 공공 실거래 구허브 — {today}</title>
<meta name=description content="{gu} 감시 단지 {n}개 국토부 공공 실거래 중위·분포·추세 한눈에. 자체 점수·순위 없음, 투자자문 아님.">
<script type="application/ld+json">{_json.dumps(breadcrumb_ld, ensure_ascii=False)}</script>
<script type="application/ld+json">{_json.dumps(dataset_ld, ensure_ascii=False)}</script>
<style>{_CSS}</style>
{ga4_snippet()}
</head><body>
<div class=wrap>
<nav class=top><a href="../index.html">구 허브</a><a href="../explorer.html">탐색기</a>
<a href="../daily/latest.html">오늘의 변화</a><a href="../methodology.html">방법론</a></nav>
<div class=crumb><a href="../index.html">서울</a> › {gu}</div>
<h1>{gu} 아파트 실거래</h1>
<p class=meta>{n}단지 · 기준 {asof} · 국토부 실거래(신고 지연 최대 30일) · 매일 자동 갱신</p>
<div class=tiles>{tiles}</div>
<div class=tblwrap><table><thead><tr>
<th>단지</th><th>전용</th><th>중위 n</th><th>P25–P75</th><th>구중위대비%</th>
<th>3/9개월</th><th>52주 위치</th><th>전세가율</th><th>회전율</th>
</tr></thead><tbody>{"".join(trs)}</tbody></table></div>
<div class=foot>
산식: 구 중위=아파트·전용40㎡+·매매표본10건+ 단지만 골라 그 단지들 중위의 중위(억). 구중위대비%=(단지 중위÷구 중위−1)×100.
3/9개월=최근 3개월 중위 vs 직전 9개월 중위(과거 비교 사실, 전망 아님). 52주 위치=최근 3개월 체결 중위가 12개월 실거래 최저~최고 레인지에서
차지하는 위치(%). 전세가율=전세 중위÷매매 중위(전세표본 5건 미만 또는 95% 초과·매매표본 10건 미만·비아파트·40㎡ 미만은 —). 회전율=12개월
거래건수÷세대수×100(%). 기준일 {asof} · 국토부 RTMS 공공데이터, 민간 시세는 사용·게재하지 않음.<br>
{be.DISCLAIMER} {be._takedown()}<br>
<a href="../methodology.html">방법론 전문</a> · <a href="../posts/{today}-{quote(gu)}.html">{gu} 최신 포스트</a> ·
코드: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a>
</div>
</div>
</body></html>"""
    assert_wording_ok(out, f"gu_hub:{gu}")
    return out
