"""단지 개별 페이지(2026-09-05 P2) — 구허브(25개)·다이제스트(1일 1편)에 이어 세 번째 표면.
A모델 무점수 원칙 그대로: 공공 실거래 사실·분포·추세·전세·회전율·구중위대비%만 게재, 순위·추천·전망 없음.

게이트(select_page_complexes) = 아파트·전용 40㎡ 이상·매매표본 30건 이상(구허브/다이제스트의 passes_rank_gate
보다 엄격 — 개별 페이지는 월별 차트까지 보여주므로 표본이 더 필요하다는 판단, 2026-09-05).

월별 중위(build_monthly_medians)는 raw MOLIT 파일이 있을 때만 채워진다(build_site.build 의 molit_path).
파일이 없으면 차트만 생략하고 나머지 블록(헤드라인·분포·전세·회전율·구대비·peers·입지)은 dataset.json 의
기게시 필드만으로 정상 렌더된다 — 이 페이지의 핵심 수치는 raw MOLIT 재계산이 아니라 발행 파이프라인이
이미 검증한 값을 재사용한다(동일 수치 두 곳에서 다르게 계산되는 사고 방지).
"""
from __future__ import annotations
import re
import json
import statistics as st
from urllib.parse import quote

import blog.build_explorer as be
from blog.wording_guard import assert_wording_ok

_CSS = (
    "*{box-sizing:border-box}body{margin:0;background:#f7f5f0;color:#1b1a17;"
    'font-family:"IBM Plex Sans KR",-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;'
    "font-size:14px;line-height:1.5;font-variant-numeric:tabular-nums}"
    "a{color:#1d6f6a;text-decoration:none}a:hover{text-decoration:underline}"
    "h1,h2,p{margin:0}"
    ".wrap{max-width:1180px;margin:0 auto;padding:20px 20px 56px}"
    "nav.top{display:flex;gap:16px;font-size:13px;color:#5c584f;margin-bottom:16px;flex-wrap:wrap}"
    ".crumb{font-size:12px;color:#8a857a;margin-bottom:6px}.crumb a{color:#8a857a}"
    "h1{font-size:26px;font-weight:700;letter-spacing:-.02em;margin-bottom:4px}"
    ".meta{font-size:13px;color:#5c584f;margin-bottom:8px}"
    ".chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}"
    ".chip{display:inline-flex;align-items:center;padding:3px 9px;border:1px solid #cfc9bc;border-radius:6px;"
    "background:#fff;font-size:12px;color:#5c584f;white-space:nowrap}"
    ".layout{display:flex;flex-direction:column;gap:20px}"
    ".main{display:flex;flex-direction:column;gap:20px;min-width:0}"
    ".aside{display:flex;flex-direction:column;gap:20px}"
    ".card{background:#fff;border:1px solid #e6e2d9;border-radius:10px;padding:18px;"
    "display:flex;flex-direction:column;gap:10px}"
    ".q{font-size:12px;font-weight:600;color:#1d6f6a;letter-spacing:.02em}"
    ".k{font-size:12px;color:#8a857a;line-height:1.3}"
    ".v{font-size:16px;font-weight:600;color:#1b1a17;line-height:1.3}"
    ".stat{display:flex;flex-direction:column;gap:2px}"
    ".statgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;"
    "padding-top:10px;border-top:1px solid #e6e2d9}"
    ".hero{font-size:38px;font-weight:700;line-height:1;letter-spacing:-.03em}"
    ".up{color:#c43d2f}.down{color:#2f5fc4}"
    ".posbar{height:10px;border-radius:5px;background:#e6e2d9;overflow:hidden;margin:4px 0}"
    ".posbar>div{height:100%;background:#1d6f6a}"
    ".jbar{display:flex;height:12px;border-radius:6px;overflow:hidden;background:#e6e2d9}"
    ".jbar>div{background:#1d6f6a}"
    ".barlabels{display:flex;justify-content:space-between;font-size:11px;color:#8a857a}"
    ".t{width:100%;border-collapse:collapse;font-size:13px}"
    ".t th{text-align:left;font-weight:500;color:#8a857a;font-size:12px;padding:6px 0;"
    "border-bottom:1px solid #cfc9bc;white-space:nowrap}"
    ".t td{padding:8px 0;border-bottom:1px solid #e6e2d9;vertical-align:top}"
    ".t .r{text-align:right;padding-left:8px;white-space:nowrap}"
    ".factgrid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px 16px}"
    ".more a{display:flex;align-items:center;justify-content:space-between;min-height:40px;"
    "border-bottom:1px solid #e6e2d9;font-weight:500}.more a:last-child{border-bottom:0}"
    ".foot{font-size:12px;color:#8a857a;margin-top:4px;line-height:1.6}"
    "details summary{cursor:pointer;font-size:12px;color:#5c584f;margin-top:4px}"
    "@media(min-width:1024px){.layout{flex-direction:row;align-items:flex-start}"
    ".main{flex:1 1 0}.aside{width:360px;flex:0 0 360px;position:sticky;top:24px}}"
    "@media(max-width:760px){.wrap{padding:14px 14px 40px}.card{padding:14px}.t{font-size:12px}}"
)


def passes_complex_page_gate(r: dict) -> bool:
    """단지 개별 페이지 게이트(2026-09-05 P2) — 아파트·전용 40㎡ 이상·매매표본 30건 이상만.
    구허브/다이제스트의 passes_rank_gate(표본 10건)보다 엄격 — 월별 차트까지 보여주는 표면이라
    한 자리 표본으로 12분할하면 달마다 n<2 가 태반이라 상향(2026-09-05 사용자 확정 스펙)."""
    return (r.get("product_type") == "아파트"
            and (r.get("area_m2") or 0) >= 40
            and (r.get("molit_n") or 0) >= 30)


def select_page_complexes(ds: dict) -> list[dict]:
    """dataset 전체에서 개별 페이지를 만들 단지만 골라 반환."""
    return [r for r in ds["complexes"] if passes_complex_page_gate(r)]


def complex_slug(gu: str, name: str) -> str:
    """단지 페이지 파일명/URL slug — '{gu}-{name}' 공백 제거(예: 노원-상계주공2단지).
    slugify_complex_name(이름만)과 별도 네임스페이스 — 파일 하나가 반드시 구를 포함해야
    서로 다른 구의 동명 단지가 충돌하지 않는다. percent-encode 는 호출측(quote) 책임 —
    디스크 파일명은 posts/ 와 동일하게 raw 한글 유지."""
    return re.sub(r"\s+", "", f"{gu}-{name}")


def select_peers(this_row: dict, gu_rows: list[dict], limit: int = 8) -> list[dict]:
    """같은 구·비슷한 전용(±10㎡)·자기 제외·매매중위 존재 단지를, 이 단지 중위와의 차이 오름차순으로
    최대 limit개. 중위가 없는 후보(매칭 표본 부족)는 '가깝다'를 정의할 수 없어 제외한다."""
    this_area = this_row.get("area_m2")
    if this_area is None:
        return []
    this_med = this_row.get("molit_recent_eok")
    this_name = this_row.get("name")
    cands = [r for r in gu_rows
             if r.get("name") != this_name
             and r.get("area_m2") is not None
             and abs(r["area_m2"] - this_area) <= 10
             and r.get("molit_recent_eok") is not None]
    if this_med is not None:
        cands.sort(key=lambda r: abs(r["molit_recent_eok"] - this_med))
    else:
        cands.sort(key=lambda r: r["name"])
    return cands[:limit]


def build_monthly_medians(recs: list[dict], asof: str) -> list[dict]:
    """이미 매칭된(동일평형 ±3.5㎡·canonical 이름매칭, be._match_records_public 산출) recs 를
    월별로 쪼개 [{ym:'YYYY-MM', n, median_eok|None}] 12개월(당월 제외, be._month_windows 재사용
    — 발행 수치와 동일 윈도우)을 오래된 달→최신 달 순으로 반환. 이상치컷은 be._median_of 와 동일하게
    '표본 전체 중위의 0.6배 미만 제외'를 12개월 전체에 한 번 적용한 뒤 월별로 쪼갠다(월별로 따로
    컷하면 표본이 작아 컷 자체가 왜곡되므로 전체 컷을 재사용 — molit_median 과 동일 표본 정의 유지)."""
    prices_all = [r["price"] for r in recs if r.get("price")]
    m0 = st.median(prices_all) if prices_all else None
    clean = [r for r in recs if r.get("price") and (m0 is None or r["price"] >= m0 * 0.6)]
    recent, prior = be._month_windows(asof)
    months = sorted(recent | prior)
    out = []
    for ym in months:
        px = [r["price"] for r in clean if r.get("ym") == ym]
        n = len(px)
        median_eok = round(st.median(px) / 1e8, 2) if n else None
        out.append({"ym": f"{ym[:4]}-{ym[4:]}", "n": n, "median_eok": median_eok})
    return out


# ── 포맷 헬퍼(구허브·다이제스트와 동일 관례 — 렌더러마다 자기 헬퍼를 갖는다) ──────

def _eok(v: float | None) -> str:
    return f"{v:g}억" if v is not None else "—"


def _pct(v: float | None) -> str:
    return f"{v:g}%" if v is not None else "—"


def _trend_cell(r: dict) -> str:
    d, p = r.get("molit_trend_dir"), r.get("molit_trend_pct")
    if p is None:
        return "—"
    if d == "—":
        return "보합"
    cls = "up" if d == "▲" else "down"
    return f'<span class="{cls}">{d}{abs(p):g}%</span>'


def _header_block(row: dict, asof: str) -> str:
    gu, name = row["gu"], row["name"]
    area, py = row.get("area_m2"), row.get("pyeong")
    if area is not None and py is None:            # pyeong 결측 방어 — 전용에서 파생(3.305785㎡/평)
        py = round(area / 3.305785, 1)
    units, byear, ptype = row.get("units"), row.get("built_year"), row.get("product_type")
    return (
        f'<div class="crumb"><a href="../index.html">홈</a> › '
        f'<a href="../gu/{quote(gu)}.html">{gu} 허브</a> › {name}</div>'
        f"<h1>{name}</h1>"
        f'<p class="meta">{gu}'
        + (f" · 전용 {area:g}㎡ ({py:g}평)" if area is not None else "")
        + (f" · {units:,}세대" if units else "")
        + (f" · {byear}년 준공" if byear else "")
        + (f" · {ptype}" if ptype else "")
        + "</p>"
        '<div class="chips">'
        f'<span class="chip">기준 {asof}</span>'
        '<span class="chip">국토부 실거래 · 신고 지연 최대 30일</span>'
        '<span class="chip">자체 점수·등급 없음</span>'
        "</div>"
    )


def _hero_card(row: dict) -> str:
    med, n = row.get("molit_recent_eok"), row.get("molit_n") or 0
    gu_med = row.get("_gu_median_eok")
    cmp_pct = round((med / gu_med - 1) * 100, 1) if (med is not None and gu_med) else None
    p25, p75 = row.get("molit_p25_eok"), row.get("molit_p75_eok")
    pos = row.get("molit_pos_52w")
    d, p = row.get("molit_trend_dir"), row.get("molit_trend_pct")

    if p is None:
        trend_word = "표본 부족으로 —"
    elif d == "—":
        trend_word = "보합"
    else:
        trend_word = f"{'상승' if d == '▲' else '하락'} {abs(p):g}%"
    badge = _trend_cell(row)
    badge_html = f'<span style="font-size:16px;font-weight:600">{badge}</span>' if badge != "—" else ""

    pos_txt = f"{pos}% 위치" if pos is not None else "표본 부족으로 위치 미산출"
    posbar = ""
    if pos is not None:
        posbar = (
            f'<div class="posbar"><div style="width:{pos}%"></div></div>'
            f'<div class="barlabels"><span>최저(0%)</span><span>12개월 범위 내 위치</span><span>최고(100%)</span></div>'
        )

    hero_num = f"{med:g}" if med is not None else "—"
    hero_unit = "억" if med is not None else ""
    pyeong_txt = f'{row["pyeong_price_man"]:,}만' if row.get("pyeong_price_man") is not None else "—"
    cmp_txt = f"{cmp_pct:+.1f}%" if cmp_pct is not None else "—"

    return (
        '<section class="card">'
        '<div class="q">Q. 지금 얼마에 거래되나</div>'
        '<div style="display:flex;align-items:baseline;gap:10px">'
        f'<div class="hero">{hero_num}<span style="font-size:19px;font-weight:600">{hero_unit}</span></div>'
        f"{badge_html}"
        "</div>"
        f'<p style="font-size:13px;color:#5c584f;line-height:1.55">최근 3개월 실거래 중위가 직전 9개월 대비 '
        f"{trend_word}(과거 비교 사실, 전망 아님). 12개월 범위 내 {pos_txt}.</p>"
        '<div class="statgrid">'
        f'<div class="stat"><span class="k">12개월 중위</span><span class="v">{_eok(med)}</span><span class="k">n{n}</span></div>'
        f'<div class="stat"><span class="k">중간 50%(P25~P75)</span><span class="v" style="font-size:14px">{_eok(p25)}~{_eok(p75)}</span></div>'
        f'<div class="stat"><span class="k">평당(12개월 중위)</span><span class="v">{pyeong_txt}</span></div>'
        f'<div class="stat"><span class="k">{row.get("gu", "")} 중위 대비</span><span class="v">{cmp_txt}</span></div>'
        "</div>"
        f"{posbar}"
        "</section>"
    )


def _chart_svg(monthly: list[dict]) -> str:
    vals = [m["median_eok"] for m in monthly if m["median_eok"] is not None]
    if not vals:
        return ""
    vmin, vmax = min(vals), max(vals)
    if vmin == vmax:
        vmin, vmax = vmin - 0.5, vmax + 0.5
    pad = (vmax - vmin) * 0.15
    vmin, vmax = vmin - pad, vmax + pad
    n = len(monthly)
    x0, x1, y0, y1 = 40, 700, 20, 170

    def xy(i, v):
        x = x0 + (i * (x1 - x0) / (n - 1) if n > 1 else 0)
        y = y1 - (v - vmin) / (vmax - vmin) * (y1 - y0)
        return x, y

    grid, ylabels = [], []
    for k in range(5):
        gy = y0 + (y1 - y0) * k / 4
        gv = vmax - (vmax - vmin) * k / 4
        grid.append(f'<line x1="{x0}" y1="{gy:.1f}" x2="{x1}" y2="{gy:.1f}"></line>')
        ylabels.append(f'<text x="{x0 - 4}" y="{gy + 3:.1f}">{gv:.2g}</text>')

    segs, circles, prev = [], [], None
    for i, m in enumerate(monthly):
        v = m["median_eok"]
        if v is None:
            prev = None
            continue
        x, y = xy(i, v)
        circles.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4"><title>{m["ym"]} · 중위{v:g}억 · {m["n"]}건</title></circle>')
        if prev is not None:
            px, py, pi = prev
            if i - pi > 1:
                segs.append(f'<path d="M{px:.1f},{py:.1f} L{x:.1f},{y:.1f}" fill="none" stroke="#1d6f6a" '
                            f'stroke-width="2" stroke-dasharray="4 4" opacity="0.5"></path>')
            else:
                segs.append(f'<path d="M{px:.1f},{py:.1f} L{x:.1f},{y:.1f}" fill="none" stroke="#1d6f6a" '
                            f'stroke-width="2" stroke-linecap="round"></path>')
        prev = (x, y, i)

    show_idx = {0, min(3, n - 1), min(6, n - 1), min(9, n - 1), n - 1}
    xlabels = []
    for i, m in enumerate(monthly):
        if i in show_idx:
            x, _ = xy(i, vmin)
            mm = m["ym"][5:7]
            mm = mm[1] if mm.startswith("0") else mm
            xlabels.append(f'<text x="{x:.1f}" y="188">{mm}월</text>')

    return (
        '<svg viewBox="0 0 740 200" style="display:block;width:100%;height:auto" role="img" '
        'aria-label="월별 중위 실거래가">'
        f'<g stroke="#e6e2d9" stroke-width="1">{"".join(grid)}</g>'
        f'<g fill="#8a857a" font-size="10" text-anchor="end">{"".join(ylabels)}</g>'
        f'{"".join(segs)}'
        f'<g fill="#fff" stroke="#1d6f6a" stroke-width="2">{"".join(circles)}</g>'
        f'<g fill="#8a857a" font-size="10" text-anchor="middle">{"".join(xlabels)}</g>'
        "</svg>"
    )


def _monthly_table(monthly: list[dict]) -> str:
    rows = "".join(
        f'<tr><td>{m["ym"]}</td><td class=r>{_eok(m["median_eok"])}</td><td class=r>{m["n"]}건</td></tr>'
        for m in monthly)
    return (
        "<details><summary>월별 표로 보기</summary>"
        '<table class="t"><thead><tr><th>월</th><th class=r>중위</th><th class=r>거래건수</th></tr></thead>'
        f"<tbody>{rows}</tbody></table></details>"
    )


def _monthly_chart_card(monthly: list[dict] | None) -> str:
    if not monthly:
        return (
            '<section class="card"><div class="q">Q. 오르는 중인가</div>'
            '<p style="font-size:13px;color:#8a857a">월별 원자료가 이 빌드에 없어 월별 그래프는 생략합니다 '
            "(위 12개월 중위·추세는 발행 파이프라인 기게시 수치라 정상 표시됩니다).</p></section>"
        )
    svg = _chart_svg(monthly)
    ym0, ym1 = monthly[0]["ym"], monthly[-1]["ym"]
    return (
        '<section class="card">'
        '<div style="display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:4px">'
        '<div class="q">Q. 오르는 중인가</div>'
        f'<p style="font-size:12px;color:#8a857a">월별 중위 실거래가 · {ym0}~{ym1} · 억원</p></div>'
        f"{svg}"
        '<p style="font-size:12px;color:#8a857a">점선 = 거래 없는 달을 건너뛴 구간. 표본이 적은 달은 '
        "중위가 불안정할 수 있습니다.</p>"
        f"{_monthly_table(monthly)}"
        "</section>"
    )


def _jeonse_card(row: dict) -> str:
    jn = row.get("jeonse_n")
    ratio = row.get("jeonse_ratio_complex_pct")
    gated = (jn or 0) >= 5 and ratio is not None and ratio <= 95
    if not gated:
        return (
            '<section class="card"><div class="q">Q. 전세와 얼마나 차이 나나</div>'
            '<p style="font-size:13px;color:#8a857a">전세 표본 5건 미만이거나 게이트 미충족이라 — 처리합니다.</p>'
            "</section>"
        )
    jm, gap, med = row.get("jeonse_recent_eok"), row.get("gap_eok"), row.get("molit_recent_eok")
    bar_w = min(ratio, 100)
    return (
        '<section class="card"><div class="q">Q. 전세와 얼마나 차이 나나</div>'
        '<div class="statgrid" style="border-top:0;padding-top:0">'
        f'<div class="stat"><span class="k">전세 중위</span><span class="v">{_eok(jm)}</span><span class="k">n{jn}</span></div>'
        f'<div class="stat"><span class="k">전세가율</span><span class="v">{_pct(ratio)}</span></div>'
        f'<div class="stat"><span class="k">매매–전세 갭</span><span class="v">{_eok(gap)}</span></div>'
        "</div>"
        f'<div class="jbar"><div style="width:{bar_w}%"></div></div>'
        '<div style="display:flex;justify-content:space-between;font-size:12px;color:#5c584f">'
        f"<span>전세 {_eok(jm)}({_pct(ratio)})</span><span>매매 {_eok(med)}</span></div>"
        "</section>"
    )


def _liquidity_card(row: dict) -> str:
    ta, tp = row.get("trade_annual"), row.get("turnover_pct")
    ta_txt = f"{ta:g}건" if ta is not None else "—"
    return (
        '<section class="card"><div class="q">Q. 거래가 잦은 단지인가</div>'
        '<div class="statgrid" style="border-top:0;padding-top:0">'
        f'<div class="stat"><span class="k">12개월 거래</span><span class="v">{ta_txt}</span></div>'
        f'<div class="stat"><span class="k">회전율</span><span class="v">{_pct(tp)}</span><span class="k">거래건수÷세대수</span></div>'
        "</div>"
        '<p style="font-size:12px;color:#8a857a">회전율이 낮다고 거래가 어렵다는 뜻은 아닙니다 '
        "(거주 만족·매물 희소 등 다양한 이유가 있을 수 있습니다).</p>"
        "</section>"
    )


_FACT_SPECS = [
    ("subway_m", "지하철", lambda v, r: f"{v:,.0f}m"),
    ("cbd_km", "업무지구", lambda v, r: f"{v:g}km" + (f" ({r['cbd_name']})" if r.get("cbd_name") else "")),
    ("nearest_elem_school", "인근 초등학교", lambda v, r: v),
    ("academy_exam", "학원가(1km)", lambda v, r: f"{v:g}곳"),
    ("maint_fee_won", "관리비", lambda v, r: f"{v:,.0f}원/월"),
    ("parking_per_unit", "세대당 주차", lambda v, r: f"{v:g}대"),
    ("heating", "난방", lambda v, r: v),
    ("far_pct", "용적률", lambda v, r: f"{v:g}%" + (f" / 건폐 {r['bcr_pct']:g}%" if r.get("bcr_pct") is not None else "")),
    ("gongsi_man", "공시가격", lambda v, r: f"{v / 10000:.2f}억"),
    ("builder", "시공사", lambda v, r: v),
]


def _facts_card(row: dict) -> str:
    items = []
    for key, label, fmt in _FACT_SPECS:
        v = row.get(key)
        if v in (None, "", "-", "—"):
            continue
        items.append(f'<div class="stat"><span class="k">{label}</span>'
                      f'<span class="v" style="font-size:14px">{fmt(v, row)}</span></div>')
    if not items:
        return ""
    return f'<section class="card"><div class="q">입지·단지</div><div class="factgrid">{"".join(items)}</div></section>'


def _peers_card(row: dict, peers: list[dict]) -> str:
    if not peers:
        return ""
    gu = row["gu"]
    trs = []
    for p in peers:
        p_slug = complex_slug(p["gu"], p["name"])
        if passes_complex_page_gate(p):
            href = f"../complex/{quote(p_slug)}.html"
        else:
            href = f'../gu/{quote(p["gu"])}.html#{quote(be.slugify_complex_name(p["name"]))}'
        trs.append(
            f'<tr><td><a href="{href}">{p["name"]}</a></td>'
            f'<td class=r>{p["area_m2"]:g}㎡</td>'
            f'<td class=r>{_eok(p.get("molit_recent_eok"))} <span class=k>n{p.get("molit_n") or 0}</span></td>'
            f'<td class=r>{_trend_cell(p)}</td>'
            f'<td class=r>{_pct(p.get("molit_pos_52w"))}</td></tr>'
        )
    return (
        '<section class="card"><div class="q">Q. 같은 구, 비슷한 전용(±10㎡) 단지와 비교하면</div>'
        '<table class="t"><thead><tr><th>단지</th><th class=r>전용</th><th class=r>중위 n</th>'
        "<th class=r>3/9개월</th><th class=r>52주</th></tr></thead>"
        f'<tbody>{"".join(trs)}</tbody></table>'
        f'<p style="font-size:12px;color:#8a857a">{gu} · 전용 ±10㎡ · 각 단지 게시 수치 기준.</p></section>'
    )


def _links_card(row: dict) -> str:
    gu = row["gu"]
    band = row.get("area_band") or be.area_band(row["area_m2"])
    return (
        '<section class="card"><div class="q">더 보기</div>'
        '<div class="more">'
        f'<a href="../gu/{quote(gu)}.html">{gu} 허브</a>'
        f'<a href="../explorer.html?gu={quote(gu)}&band={quote(band)}">탐색기 · {gu} {band}</a>'
        '<a href="../methodology.html">방법론 전문</a>'
        '<a href="../daily/latest.html">최신 다이제스트</a>'
        "</div></section>"
    )


def _foot_block(asof: str) -> str:
    return (
        '<div class="foot">'
        "산식: 12개월 중위=전용 ±3.5㎡ 동일평형·canonical 이름매칭 12개월 실거래 중위(이상치 −40%컷). "
        "3/9개월=최근 3개월 중위 vs 직전 9개월 중위(과거 비교 사실, 전망 아님). "
        "52주 위치=최근 3개월 체결 중위가 12개월 실거래 최저~최고 레인지에서 차지하는 위치(%). "
        "전세가율=전세 중위÷매매 중위(전세표본 5건 미만 또는 95% 초과 시 —). "
        "회전율=12개월 거래건수÷세대수×100(%). 구 중위 대비%=(단지 중위÷구 중위−1)×100."
        f" 기준일 {asof}. 국토부 RTMS 공공데이터, 민간 시세는 사용·게재하지 않음.<br>"
        f"{be.DISCLAIMER} {be._takedown()}"
        "</div>"
    )


def render_complex_page(row: dict, peers: list[dict], monthly: list[dict] | None, asof: str, today: str) -> str:
    """단지 개별 페이지(전체 HTML) — row 는 dataset.json 의 그 단지 행(+ 선택적으로 build_site 가
    주입하는 '_gu_median_eok': 구 대비% 계산용, render_complex_page 자체는 구 전체 표본을 받지
    않으므로 호출측이 미리 계산해 얹는다). monthly=None 이면 월별 차트 카드만 안내문으로 대체."""
    from blog.build_site import BASE_URL, ga4_snippet  # lazy: build_site 가 본 모듈을 import(순환 예방)

    gu, name = row["gu"], row["name"]
    slug = complex_slug(gu, name)
    area = row.get("area_m2")

    breadcrumb_ld = {
        "@context": "https://schema.org", "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": 1, "name": "서울 부동산 데이터 스냅샷", "item": f"{BASE_URL}/"},
            {"@type": "ListItem", "position": 2, "name": gu, "item": f"{BASE_URL}/gu/{quote(gu)}.html"},
            {"@type": "ListItem", "position": 3, "name": name, "item": f"{BASE_URL}/complex/{quote(slug)}.html"},
        ]}
    var_measured = ["molit_recent_eok(12개월 동일평형 중위, 억)", "molit_p25_eok", "molit_p75_eok",
                    "molit_trend_pct", "molit_pos_52w"]
    if monthly:
        var_measured += [f"monthly_median_{m['ym']}" for m in monthly]
    area_txt = f"{area:g}㎡" if area is not None else ""
    dataset_ld = {
        "@context": "https://schema.org", "@type": "Dataset",
        "name": f"{name}({gu}) 아파트 공공 실거래 {today}",
        "description": f"{name} 전용{area_txt} 12개월 국토부 공공 실거래 중위·분포·추세·월별 중위.",
        "dateModified": today, "license": "https://creativecommons.org/licenses/by-nc/4.0/",
        "creator": {"@type": "Organization", "name": "agent_realestate (개인 연구)"},
        "isAccessibleForFree": True, "keywords": ["부동산", "실거래", "공공데이터", "서울", gu, name],
        "variableMeasured": var_measured}

    body = (
        _header_block(row, asof)
        + '<div class="layout"><div class="main">'
        + _hero_card(row)
        + _monthly_chart_card(monthly)
        + _jeonse_card(row)
        + _liquidity_card(row)
        + _peers_card(row, peers)
        + '</div><div class="aside">'
        + _facts_card(row)
        + _links_card(row)
        + "</div></div>"
        + _foot_block(asof)
    )

    out = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{name} 실거래 — {gu} · {today}</title>
<meta name=description content="{name}({gu}) 전용{area_txt} 국토부 공공 실거래 12개월 중위·분포·추세·월별 중위. 자체 점수·순위 없음, 투자자문 아님.">
<script type="application/ld+json">{json.dumps(breadcrumb_ld, ensure_ascii=False)}</script>
<script type="application/ld+json">{json.dumps(dataset_ld, ensure_ascii=False)}</script>
<style>{_CSS}</style>
{ga4_snippet()}
</head><body>
<div class=wrap>
<nav class=top><a href="../index.html">구 허브</a><a href="../explorer.html">탐색기</a>
<a href="../daily/latest.html">오늘의 변화</a><a href="../methodology.html">방법론</a></nav>
{body}
</div>
</body></html>"""
    assert_wording_ok(out, f"complex_page:{gu}/{name}")
    return out
