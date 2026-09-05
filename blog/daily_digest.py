"""일간 다이제스트(2026-09-05 P1) — 티스토리 1편 + 사이트 1페이지, 25구 통합 덤프 대체.
선택 로직(gate)은 report/blog-growth-2026-09-05/canvas/gen_digest.py 프로토타입을 그대로 계승:
아파트·전용 40㎡+·매매표본 10건+ 만 순위 대상(build_explorer.passes_rank_gate 등 단일소스).
A모델 무점수 — 정렬·상단/하단 근접은 관측된 사실 위치일 뿐 추천이 아니다(wording_guard 로 강제).
"""
from __future__ import annotations
import json
from urllib.parse import quote

import blog.build_explorer as be
from blog.build_site import BASE_URL, ga4_snippet
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
    ".tblwrap{overflow-x:auto;background:#fff;border:1px solid #e6e2d9;border-radius:10px;margin-top:4px}"
    "table{width:100%;border-collapse:collapse;font-size:13px}"
    "th,td{padding:7px 9px;border-bottom:1px solid #e6e2d9;text-align:right;white-space:nowrap}"
    "th{color:#5c584f;font-weight:500;font-size:12px;border-bottom:1px solid #cfc9bc}"
    "td:first-child,th:first-child{text-align:left;white-space:normal}"
    "tbody tr:hover{background:#faf9f6}"
    ".foot{font-size:12px;color:#8a857a;margin-top:18px;line-height:1.6}"
    "@media(max-width:760px){.wrap{padding:14px 14px 40px}.tiles{gap:8px}"
    ".tile{flex:1 1 45%;padding:10px 12px}table{font-size:12px}th,td{padding:6px 7px}}"
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


def _today_counts(ds: dict) -> dict:
    cx = ds["complexes"]
    return {
        "n_total": ds.get("count", len(cx)),
        "n_sample": sum(r.get("molit_n") or 0 for r in cx),
        "up": sum(1 for r in cx if r.get("molit_trend_dir") == "▲"),
        "down": sum(1 for r in cx if r.get("molit_trend_dir") == "▼"),
        "flat": sum(1 for r in cx if r.get("molit_trend_dir") == "—"),
    }


def _render_tistory(today, asof, counts, sel, gu_rows) -> str:
    def name_cell(r):
        return (f'<a href="{_hub_url_abs(r)}"><b>{r["name"]}</b></a>({r["gu"]})'
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
    parts = [
        f'<p><b>오늘의 숫자</b> — 기준일 {asof} · 발행 {counts["n_total"]}단지 · 표본 {counts["n_sample"]}건 · '
        f'상승 {counts["up"]} · 하락 {counts["down"]} · 보합 {counts["flat"]}(국토부 실거래 사실, 자체 점수 없음)</p>',
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
        f'3/9개월=최근 3개월 중위 vs 직전 9개월 중위(과거 비교 사실, 전망 아님). '
        f'52주 위치=최근 3개월 체결 중위의 12개월 실거래 최저~최고 레인지 내 위치(%). '
        f'회전율=12개월 거래건수÷세대수×100(%). 구 중위=게이트 통과 단지 중위의 중위(억). '
        f'기준일 {asof}, 표본수는 각 셀 n 표기. 국토부 RTMS 공공데이터, 민간 시세는 사용·게재하지 않음.</p>',
        f'<p style="{_MUT}">{be.DISCLAIMER} {be._takedown()}</p>',
        f'<p><a href="{BASE_URL}/">전체 탐색기·인덱스</a> · <a href="{BASE_URL}/methodology.html">방법론 전문</a></p>',
    ]
    return "".join(parts)


def _render_site(today, asof, counts, sel, gu_rows, title) -> str:
    def name_cell(r):
        return (f'<a href="{_hub_url_rel(r)}"><b>{r["name"]}</b></a> <span class=mut>({r["gu"]})</span>'
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
<a href="../daily/latest.html">오늘의 변화</a><a href="../methodology.html">방법론</a></nav>
<div class=crumb><a href="../index.html">서울</a> › 오늘의 변화</div>
<h1>서울 아파트 오늘의 변화</h1>
<p class=meta>기준일 {asof} · 발행 {counts['n_total']}단지 · 표본 {counts['n_sample']}건</p>

<h2>오늘의 숫자</h2>
<div class=tiles>
<div class=tile><span class=k>발행 단지</span><span class=v>{counts['n_total']}</span></div>
<div class=tile><span class=k>표본 합계</span><span class=v>{counts['n_sample']:,}</span></div>
<div class=tile><span class=k>상승</span><span class="v up">▲{counts['up']}</span></div>
<div class=tile><span class=k>하락</span><span class="v down">▼{counts['down']}</span></div>
<div class=tile><span class=k>보합</span><span class=v>—{counts['flat']}</span></div>
</div>

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
회전율은 회전율 값 존재). 3/9개월=최근 3개월 중위 vs 직전 9개월 중위(과거 비교 사실, 전망 아님). 52주 위치=최근 3개월 체결 중위의
12개월 실거래 최저~최고 레인지 내 위치(%). 회전율=12개월 거래건수÷세대수×100(%). 구 중위=게이트 통과 단지 중위의 중위(억).
기준일 {asof}. 국토부 RTMS 공공데이터, 민간 시세는 사용·게재하지 않음.<br>
{be.DISCLAIMER} {be._takedown()}<br>
<a href="../methodology.html">방법론 전문</a> · <a href="../explorer.html">탐색기</a> ·
코드: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a>
</div>
</div>
</body></html>"""


def build_daily_digest(ds: dict, today: str, asof: str) -> dict:
    """다이제스트 산출 — {"title","tags","tistory_html","site_html","summary"}.
    tistory_html 은 30,000바이트 예산을 넘거나 금칙어가 섞이면 ValueError 로 발행을 막는다."""
    sel = _select_ranked(ds)
    gu_rows = _gu_summary_rows(ds)
    counts = _today_counts(ds)
    n_hi, n_lo = len(sel["hi"]), len(sel["lo"])

    title = f"서울 아파트 오늘의 변화 — {today} · 12개월 범위 상단 {n_hi}곳·하단 {n_lo}곳 · {counts['n_total']}단지"
    tags = TISTORY_TAGS + ",오늘의변화"
    summary = (f"{today} 기준 {counts['n_total']}단지 · 표본 {counts['n_sample']}건 · "
               f"상승 {counts['up']}·하락 {counts['down']}·보합 {counts['flat']} · "
               f"12개월 범위 상단 근접 {n_hi}곳·하단 근접 {n_lo}곳")

    tistory_html = _render_tistory(today, asof, counts, sel, gu_rows)
    site_html = _render_site(today, asof, counts, sel, gu_rows, title)

    assert_wording_ok(tistory_html, "daily_digest:tistory_html")
    assert_wording_ok(site_html, "daily_digest:site_html")

    tb = len(tistory_html.encode("utf-8"))
    if tb > 30000:
        raise ValueError(f"[daily_digest] tistory_html {tb}B > 30000B 예산 초과 — 섹션을 줄이세요")

    return {"title": title, "tags": tags, "tistory_html": tistory_html,
            "site_html": site_html, "summary": summary}
