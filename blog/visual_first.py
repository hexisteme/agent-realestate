"""시각 우선 일간 글(2026-10-10) — 관측요약 → 시장흐름대시보드 → 읽는법·값 표 → 지리타일맵.

run_daily 는 RE_VISUAL_FIRST=1 일 때만 prepare_visual_first 로 대시보드 PNG 를 만들어 build_daily_digest 에 넘긴다.
넘기지 않으면 일간 글은 기존 본문과 바이트 단위로 같다. 이미지 밖에도 같은 값(값 표)과 의미·산식(읽는법)을 둔다
— 티스토리는 alt 를 지우므로 이미지가 사라져도 글만으로 읽혀야 한다."""
from __future__ import annotations

from dataclasses import dataclass
import html
from pathlib import Path
from urllib.parse import quote

from blog import build_explorer as be
from blog import market_charts as mch
from blog import market_visuals as mv

BODY_IMAGE_RESERVE = 1000  # 티스토리 재호스팅 figure 래퍼(실측 +653~800B) 몫 — 본문 예산에서 이미지마다 뺀다

# 지리타일맵(GeoTileMap): 5행×6열. 같은 열은 북→남, 같은 행은 서→동 순서를 지킨다(구청 좌표 기준 테스트).
SEOUL_GEO_TILES = {
    "은평": (0, 1), "강북": (0, 3), "도봉": (0, 4), "노원": (0, 5),
    "서대문": (1, 1), "종로": (1, 2), "성북": (1, 3), "동대문": (1, 4), "중랑": (1, 5),
    "강서": (2, 0), "마포": (2, 1), "중구": (2, 2), "성동": (2, 3), "광진": (2, 4), "강동": (2, 5),
    "양천": (3, 0), "영등포": (3, 1), "용산": (3, 2), "서초": (3, 3), "강남": (3, 4), "송파": (3, 5),
    "구로": (4, 0), "금천": (4, 1), "동작": (4, 2), "관악": (4, 3),
}
_MUTED = "#64748b"
_HEAD = "background-color:#f1f5f9;color:#0f172a"


@dataclass(frozen=True)
class VisualFirst:
    dashboard: mch.MarketDashboard
    image: mch.DashboardImage


def prepare_visual_first(snapshot: dict | None, today: str, images_dir: str | Path) -> VisualFirst | None:
    """거시 스냅샷 → 대시보드 모델 + PNG. 그릴 패널이 하나도 없거나 스냅샷이 오래됐으면 None(파일도 쓰지 않음)."""
    dashboard = mch.build_market_dashboard(snapshot, today)
    if dashboard is None:
        return None
    return VisualFirst(dashboard, mch.write_dashboard_png(dashboard, images_dir))


def body_media(visual: VisualFirst, base_url: str) -> list[dict]:
    """본문이미지계약(BodyMedia) 항목 — 원고 URL·사이트 상대경로·내용 해시·원본 크기."""
    name = visual.image.name
    return [{"url": f"{base_url}/images/{name}", "image": f"../images/{name}", "sha256": visual.image.sha256,
             "width": visual.image.width, "height": visual.image.height}]


def dashboard_alt(d: mch.MarketDashboard) -> str:
    parts = []
    if d.sale:
        parts.append(f"{mch._month_label(d.sale.latest[0])} 매매가격지수 {d.sale.latest[1]:.1f}")
    if d.trades:
        parts.append(f"매매 신고 {d.trades.latest[1]:,.0f}호")
    if d.mortgage:
        parts.append(f"주담대 신규취급 금리 {d.mortgage.latest[1]:.2f}%")
    return f"서울 아파트 시장 흐름 그래프(최근 {mch.DASHBOARD_MONTHS}개월): " + ", ".join(parts) + ". 같은 값은 아래 표에 있습니다."


def _short(month: str) -> str:
    return f"{month[2:4]}.{month[5:7]}"


# ── 지리타일맵 ─────────────────────────────────────────────────────────────────

def _text_color(background: str) -> str:
    """배경과 대비가 큰 쪽(검정·흰색) — 어느 배경이든 4.5:1 이상."""
    luminance = mv._luminance(background)
    return "#000000" if (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) else "#ffffff"


count_gated_trends = be.count_gated_trends  # 표본기준추세수 — 정본은 build_explorer 하나


def _summary_lines(counts: dict, d, asof: str, ds: dict) -> list[str]:
    rows = ds.get("complexes") or []
    gated = count_gated_trends(rows)
    return mch.observation_summary_lines({"n_total": counts.get("n_total"), **gated,
                                          "funnel": be.compute_observation_funnel(rows)}, d, asof=asof)


def _tiles(ds: dict):
    by_gu: dict[str, list[dict]] = {}
    for r in ds.get("complexes") or []:
        by_gu.setdefault(r.get("gu"), []).append(r)
    rows = {}
    for row in mv._district_rows(ds):
        gated = count_gated_trends(by_gu.get(row["gu"], []))
        rows[row["gu"]] = {**row, "up": gated["up"], "down": gated["down"]}
    scale = mv._scales([rows[gu] for gu in SEOUL_GEO_TILES if gu in rows])["price"]
    return rows, scale


_TILE_KEY = "구 이름 · 구 중위가격 · ▲ 높아진 단지 수 · ▼ 낮아진 단지 수"


def render_geo_tile_heatmap(ds: dict, asof: str, *, surface: str) -> str:
    """25개 구를 실제 위치에 가깝게 놓은 가격 지도. 칸 글자색을 배경 대비로 명시해 어두운 스킨에서도 읽힌다."""
    rows, scale = _tiles(ds)
    end = mv._TISTORY_PRICE_END if surface == "tistory" else mv._PRICE_END
    cells = {}
    for gu, (r, c) in SEOUL_GEO_TILES.items():
        row = rows.get(gu, {"price": None, "up": 0, "down": 0, "n": 0})
        bg = mv._color(row["price"], scale, end=end)
        price = f"{row['price']:.1f}억" if row["price"] is not None else "—"
        cells[(r, c)] = (gu, row, bg, _text_color(bg), price)
    note = (f"기준일 {html.escape(asof)} · 색이 진할수록 구 중위가격이 높습니다(0~{mv._fmt(scale)}억). "
            "구 중위는 게이트 통과 단지 중위의 중위, ▲·▼는 표본 기준(아파트·전용 40㎡ 이상·매매 10건 이상)을 채운 단지 중 "
            "최근 3개월 실거래 중위가 직전 9개월보다 높거나 낮은 단지 수입니다. "
            "회색 —는 표본 없음." + ("" if surface == "tistory" else " 위치는 실제 지도를 단순화한 배치입니다."))
    if surface == "tistory":
        # U2(외부 리뷰 2026-10-13, Codex·agy 합의) — 6열 격자는 390px에서 57px 칸·4줄이 된다. 티스토리는 가나다순 목록 표,
        # 사이트는 지도를 유지한다. 중위 칸 배경색으로 가격 높낮이를 남기고 글자색은 배경 대비로 명시한다.
        # 본문 예산(28KB) 때문에 칸 스타일은 중위 칸 배경·글자색만 둔다(25행).
        trs = "".join(
            f'<tr><td>{html.escape(gu)}</td>'
            f'<td data-district="{html.escape(gu)}" style="background-color:{bg};color:{fg}">{price}</td>'
            f'<td>▲{row["up"]}</td><td>▼{row["down"]}</td></tr>'
            for gu, row, bg, fg, price in sorted(cells.values(), key=lambda c: c[0]))
        head = "".join(f'<td style="{_HEAD}"><b>{h}</b></td>' for h in ("구", "구 중위", "▲ 높아짐", "▼ 낮아짐"))
        return ('<p><b>구별 가격·추세</b> <span style="color:#64748b">· 서울 25개 구 가나다순</span><br>'
                f'<span style="color:{_MUTED}">{_TILE_KEY}</span></p>'
                '<table data-district-list style="width:100%;table-layout:fixed;border-collapse:collapse">'
                f'<tr>{head}</tr>{trs}</table>'
                f'<p><span style="color:{_MUTED}">{note}</span></p>')
    tiles = []
    for (r, c), (gu, row, bg, fg, price) in sorted(cells.items()):
        label = (f"{gu} 구 중위 {price}" if row["price"] is not None else f"{gu} 표본 없음") + \
                f", 상승 {row['up']}·하락 {row['down']} 단지"
        tiles.append(f'<a role="listitem" class="geo-tile" data-district="{html.escape(gu)}" href="../gu/{quote(gu)}.html" '
                     f'style="grid-row:{r + 1};grid-column:{c + 1};background-color:{bg};color:{fg}" '
                     f'aria-label="{html.escape(label)}"><b>{html.escape(gu)}</b><span>{price}</span>'
                     f'<span>▲{row["up"]} ▼{row["down"]}</span></a>')
    return ('<section class="geo" data-geo-tiles="districts" aria-labelledby="geo-title">'
            f'<h2 id="geo-title">구별 가격 지도</h2><p class="vf-note">{_TILE_KEY}</p><div class="geo-grid" role="list">' + "".join(tiles) + '</div>'
            f'<p class="vf-note">{note}</p></section>')


# ── 티스토리 상단 ──────────────────────────────────────────────────────────────

def _table_rows(d: mch.MarketDashboard):
    return [(_short(m), *values) for m, *values in mch.dashboard_table_rows(d)]


def _briefing_html(briefing) -> str:
    """상단 요약에 잇는 사실 리드(U9) — 관측 요약 바로 아래 '• ' 줄."""
    return "".join(f"<br>• {html.escape(text)}" for text in briefing or ())


def dashboard_caption(d: mch.MarketDashboard) -> str:
    """그래프 아래 보이는 설명(U3, 티스토리는 alt 를 지운다) — 패널별 기간과 시작→최근 값."""
    parts, first, last = [], None, None
    for label, panel, fmt in (("매매지수", d.sale, "{:.1f}"), ("전세지수", d.jeonse, "{:.1f}"),
                              ("매매 신고", d.trades, "{:,.0f}호"), ("주담대 금리", d.mortgage, "{:.2f}%")):
        if not panel or not panel.points:
            continue
        (m0, v0), (m1, v1) = panel.points[0], panel.points[-1]
        first, last = min(first or m0, m0), max(last or m1, m1)
        parts.append(f"{label} {fmt.format(v0)}→{fmt.format(v1)}")
    if not parts:
        return ""
    return f"그래프 기간 {first}~{last}: " + " · ".join(parts) + "."


def render_tistory_top(visual: VisualFirst, counts: dict, asof: str, ds: dict, base_url: str,
                       briefing=()) -> str:
    d = visual.dashboard
    summary = "<br>".join(html.escape(line) for line in _summary_lines(counts, d, asof, ds)) + _briefing_html(briefing)
    image = visual.image
    td = "padding:4px 2px;text-align:center"
    head = "".join(f'<td style="{_HEAD};{td}"><b>{h}</b></td>'
                   for h in ("월", "매매지수", "전세지수", "거래(호)", "주담대(%)"))
    # U4 — 월 칸만 줄바꿈 금지('26.03' 5자). 다른 칸 nowrap 은 좁은 화면에서 표 넘침을 만들 수 있다(Codex R1).
    body = "".join("<tr>" + "".join(f'<td style="{td}{";white-space:nowrap" if i == 0 else ""}">{v}</td>'
                                    for i, v in enumerate(row)) + "</tr>" for row in _table_rows(d))
    caption = dashboard_caption(d)
    notes = "".join(f'<p><b>{html.escape(title)}</b> — {html.escape(meaning)}<br>'
                    f'<span style="color:{_MUTED}">{html.escape(formula)}</span></p>'
                    for title, meaning, formula in mch.reading_notes(d))
    return ('<p><b>오늘의 관측 요약</b></p>'
            f'<p>{summary}</p>'
            f'<p><img src="{base_url}/images/{image.name}" width="{image.width}" height="{image.height}" '
            f'alt="{html.escape(dashboard_alt(d))}"></p>'
            + (f'<p data-dashboard-caption style="color:{_MUTED}">{html.escape(caption)}</p>' if caption else "")
            + f'<p><b>최근 6개월 값</b> <span style="color:{_MUTED}">· 그래프와 같은 원자료</span></p>'
            '<table data-dashboard-table style="width:100%;table-layout:fixed;border-collapse:collapse">'
            f'<tr>{head}</tr>{body}</table>'
            '<p><b>읽는 법</b></p>' + notes
            + render_geo_tile_heatmap(ds, asof, surface="tistory"))


def compact_macro_tistory(macro: dict | None) -> str:
    """거시 지표 표에서 대시보드와 겹치는 '함께 볼 지표 5가지'를 뺀다. 미수집 안내는 그대로 둔다."""
    if not macro:
        return ""
    if not macro.get("cards"):
        return macro.get("tistory_html", "")
    from blog.macro_context import render_macro_strip_tistory
    return render_macro_strip_tistory(macro, housing=False)


# ── 사이트 상단 ────────────────────────────────────────────────────────────────

SITE_CSS = (
    ".vf-summary{font-size:1.02rem;line-height:1.6;margin:12px 0}"
    ".vf-dash{margin:12px 0}.vf-dash img{display:block;width:100%;max-width:720px;height:auto;margin:0;"
    "border:1px solid #e2e8f0;border-radius:8px}"
    ".vf-notes p{margin:6px 0;line-height:1.55}.vf-note{color:#475569;font-size:.86rem;line-height:1.5}"
    ".tip{display:inline-flex;align-items:center;justify-content:center;min-width:28px;min-height:28px;margin-left:2px;"
    "padding:0 4px;border:0;border-radius:14px;background:#e2e8f0;color:#0f172a;font:inherit;font-size:.8rem;cursor:help}"
    ".tipwrap{position:relative;display:inline-block}"
    ".tip-body{display:none;position:absolute;z-index:5;left:50%;transform:translateX(calc(-50% + var(--dx,0px)));top:calc(100% + 6px);"
    "width:min(260px,80vw);padding:8px 10px;border-radius:6px;background:#0f172a;color:#f8fafc;font-size:.82rem;"
    "font-weight:400;line-height:1.5;text-align:left;box-shadow:0 4px 14px rgba(15,23,42,.25)}"
    ".tipwrap:hover .tip-body,.tip:focus-visible+.tip-body,.tip[aria-expanded=true]+.tip-body{display:block}"
    ".tipwrap[data-tip-hidden] .tip-body{display:none}"
    # 버튼과 툴팁 사이 6px 틈을 지나도 hover 가 풀리지 않게 보이지 않는 다리를 깐다.
    ".tip-body::before{content:'';position:absolute;left:0;right:0;top:-8px;height:8px}"
    ".vf-table{width:100%;max-width:720px;border-collapse:collapse;font-variant-numeric:tabular-nums}"
    ".vf-table th,.vf-table td{padding:6px 4px;text-align:center;border-bottom:1px solid #e2e8f0}"
    ".vf-table th{background:#f1f5f9;color:#0f172a;font-weight:600}"
    # 공용 .tblwrap 은 넓은 단지 표용(최소 540px·첫 열 140px) — 5열 값 표는 폰 폭에 맞춘다.
    ".tblwrap .vf-table{min-width:0}.tblwrap .vf-table td:first-child,.tblwrap .vf-table th:first-child"
    "{min-width:0;text-align:center;white-space:nowrap}"
    ".vf-table th{white-space:normal;word-break:keep-all;padding:6px 2px}"
    ".geo-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:3px;max-width:720px}"
    ".geo-tile{display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:56px;"
    "padding:4px 1px;border-radius:6px;font-size:.78rem;line-height:1.3;text-decoration:none;word-break:keep-all}"
    ".geo-tile:focus-visible{outline:3px solid #0f172a;outline-offset:1px}"
    ".geo .vf-note{margin-top:8px;max-width:720px}"
    "@media (min-width:720px){.geo-tile{min-height:68px;font-size:.9rem}}"
)

# 툴팁은 버튼 중앙 기준이라 좁은 화면 가장자리에서 잘린다 — 열릴 때 화면 안(좌우 8px)으로 밀어 넣는다.
_TIP_SCRIPT = (
    "<script>function tipPlace(w){var b=w&&w.querySelector('.tip-body');if(!b)return;b.style.setProperty('--dx','0px');"
    "var r=b.getBoundingClientRect();if(!r.width)return;var v=document.documentElement.clientWidth,d=0;"
    "if(r.left<8)d=8-r.left;else if(r.right>v-8)d=v-8-r.right;b.style.setProperty('--dx',d+'px')}"
    "['mouseover','focusin'].forEach(function(k){document.addEventListener(k,function(e){"
    "var w=e.target.closest&&e.target.closest('.tipwrap');if(w&&!w.contains(e.relatedTarget)){w.removeAttribute('data-tip-hidden');tipPlace(w)}})});"
    "document.addEventListener('click',function(e){var t=e.target.closest('.tip');"
    "document.querySelectorAll('.tip[aria-expanded=true]').forEach(function(b){if(b!==t)b.setAttribute('aria-expanded','false')});"
    "if(t){var open=t.getAttribute('aria-expanded')!=='true';t.setAttribute('aria-expanded',open?'true':'false');"
    "if(open){t.parentNode.removeAttribute('data-tip-hidden');tipPlace(t.parentNode)}"
    "else t.parentNode.setAttribute('data-tip-hidden','')}});"
    "document.addEventListener('keydown',function(e){if(e.key==='Escape')document.querySelectorAll('.tipwrap')"
    ".forEach(function(w){w.firstChild.setAttribute('aria-expanded','false');w.setAttribute('data-tip-hidden','')})});</script>"
)


def _tip(tip_id: str, text: str) -> str:
    return (f'<span class="tipwrap"><button type="button" class="tip" aria-expanded="false" aria-describedby="{tip_id}" '
            f'aria-label="산식 보기">ⓘ</button><span role="tooltip" id="{tip_id}" class="tip-body">{html.escape(text)}</span></span>')


def render_site_top(visual: VisualFirst, counts: dict, asof: str, ds: dict, briefing=()) -> str:
    d = visual.dashboard
    summary = "<br>".join(html.escape(line) for line in _summary_lines(counts, d, asof, ds)) + _briefing_html(briefing)
    caption = dashboard_caption(d)
    image = visual.image
    notes = mch.reading_notes(d)
    formula = {title: f for title, _, f in notes}
    tips = {"index": formula.get("매매·전세 가격지수"), "trades": formula.get("아파트 매매거래량"),
            "rate": formula.get("주택담보대출 금리")}
    note_html = "".join(
        f'<p><b>{html.escape(title)}</b> {html.escape(meaning)}'
        f'{_tip("tip-note-" + str(i), f)}</p>' for i, (title, meaning, f) in enumerate(notes))

    def th(label, key):
        return f'<th scope="col">{label}{_tip("tip-" + key, tips[key]) if tips.get(key) else ""}</th>'
    head = ('<th scope="col">월</th>' + th("매매지수", "index") + '<th scope="col">전세지수</th>'
            + th("거래(호)", "trades") + th("주담대(%)", "rate"))
    rows = "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in _table_rows(d))
    return (f'<section class="vf" data-visual-first><style>{SITE_CSS}</style>'
            f'<p class="vf-summary">{summary}</p>'
            f'<figure class="vf-dash"><img src="../images/{image.name}" width="{image.width}" height="{image.height}" '
            f'alt="{html.escape(dashboard_alt(d))}" decoding="async">'
            + (f'<figcaption class="vf-note" data-dashboard-caption>{html.escape(caption)}</figcaption>' if caption else "")
            + '</figure>'
            '<h2>최근 6개월 값</h2><div class=tblwrap><table class="vf-table" data-dashboard-table>'
            f'<caption class="vf-note">그래프와 같은 원자료 · ⓘ에 산식</caption><tr>{head}</tr>{rows}</table></div>'
            f'<div class="vf-notes"><h2>읽는 법</h2>{note_html}</div>'
            + render_geo_tile_heatmap(ds, asof, surface="site") + _TIP_SCRIPT + '</section>')
