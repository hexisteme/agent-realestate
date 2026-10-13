"""Source-bound district comparisons and observation charts, without JavaScript."""
from __future__ import annotations

from datetime import date, timedelta
import html
import math
import re
import statistics
from urllib.parse import urlsplit

from blog import build_explorer as be


_DISTRICTS = tuple(sorted(be.GU_LAWD))
_MISSING_COLOR = "#e5e7eb"
_PRICE_END = (55, 142, 132)
# Tistory keeps only background-color on cells, so the skin's #333333 text must stay 4.5:1 on the darkest shade.
_TISTORY_PRICE_END = (95, 170, 160)
_CSS = (
    ".market-visuals{min-width:0;max-width:100%;margin:24px 0}"
    ".market-visuals .market-scroll{max-width:100%;overflow-x:auto;overscroll-behavior-inline:contain}"
    ".market-visuals svg{display:block;width:100%;min-width:760px;height:auto;background:#fff}"
    ".market-visuals .market-heatmap{min-width:630px;width:100%;border-collapse:collapse}"
    ".market-visuals .market-heatmap th,.market-visuals .market-heatmap td{padding:10px;"
    "white-space:normal;word-break:keep-all;text-align:left;border-bottom:1px solid #dce5e6}"
    ".market-visuals .market-heatmap td:first-child{min-width:86px}"
    ".market-visuals .market-note{font-size:13px;line-height:1.7;color:#526473;margin:10px 0}"
    ".market-visuals .market-legend{display:flex;flex-wrap:wrap;gap:8px 18px;font-size:13px;margin:10px 0}"
    ".market-visuals .market-legend span{padding:6px 10px;border-radius:5px;background:#edf6f5}"
    ".market-visuals .market-trends{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,440px),1fr));gap:16px}"
    ".market-visuals .market-series{min-width:0;background:#fff;border:1px solid #dce5e6;border-radius:10px;padding:14px}"
    ".market-visuals .market-series svg{min-width:560px}"
    ".market-visuals .market-series h3{font-size:16px;margin:0 0 8px;word-break:keep-all}"
    ".market-visuals summary{cursor:pointer;min-height:44px;line-height:1.7}"
    ".market-visuals .market-source{overflow-wrap:anywhere}"
    "@media(max-width:1100px){.market-visuals .market-trends{grid-template-columns:1fr}}"
)
_MACRO_RULES = {
    "bok_base": ("%", "event"), "fed_target_hi": ("%", "daily"),
    "fed_target_lo": ("%", "daily"), "us10y": ("%", "daily"), "us2y": ("%", "daily"),
    "cofix_new": ("%", "monthly"), "cofix_bal": ("%", "monthly"),
    "kr_govt3y": ("%", "daily"), "cd91": ("%", "daily"), "usdkrw": ("원", "daily"),
    "gold_krw_g": ("원/g", "daily"), "gold_usd_oz": ("$/oz", "daily"),
    "us_mortgage30": ("%", "weekly"), "kr_govt10y_m": ("%", "monthly"),
    "kr_m2": ("십억원", "monthly"), "us_m2": ("십억달러", "monthly"),
    "kr_mortgage_new": ("%", "monthly"), "seoul_apt_sale": ("index", "monthly"),
    "seoul_apt_jeonse": ("index", "monthly"), "seoul_apt_sales": ("호", "monthly"),
    "seoul_unsold": ("호", "monthly"),
}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _fmt(value):
    if value is None:
        return "—"
    return str(value).removesuffix(".0")


def _escape(value):
    return html.escape(str(value), quote=True)


def _valid_rank(row):
    area, n, price = (_number(row.get(key)) for key in ("area_m2", "molit_n", "molit_recent_eok"))
    return bool(area is not None and n is not None and price is not None and price > 0
                and be.passes_rank_gate(row))


def _district_rows(ds):
    rows = ds.get("complexes") if isinstance(ds, dict) else None
    by = {gu: [] for gu in _DISTRICTS}
    for row in rows if isinstance(rows, list) else []:
        gu = row.get("gu") if isinstance(row, dict) else None
        if isinstance(gu, str) and gu.strip():
            by.setdefault(gu, []).append(row)
    result = []
    for gu, rows in sorted(by.items()):
        priced = [r for r in rows if _valid_rank(r)]
        rented = [r for r in priced if _number(r.get("jeonse_n")) is not None
                  and _number(r.get("jeonse_ratio_complex_pct")) is not None
                  and r["jeonse_ratio_complex_pct"] >= 0 and be.passes_jeonse_gate(r)]
        trends = [r for r in priced if _number(r.get("molit_trend_pct")) is not None
                  and all(_number(r.get(k)) is not None and r[k] >= 2
                          for k in ("molit_trend_n_recent", "molit_trend_n_prior"))]
        gated = be.count_gated_trends(rows)  # 표본기준추세수 — 제목·관측요약과 같은 기준(C1)
        result.append({"gu": gu, "n": len(rows), "n_judged": gated["n_judged"], "up": gated["up"], "down": gated["down"],
                       "price": _number(be.compute_gu_median(priced)), "price_n": len(priced),
                       "jeonse": be.compute_gu_jeonse_ratio_median(rented), "jeonse_n": len(rented),
                       "trend": _number(round(statistics.median(r["molit_trend_pct"] for r in trends), 1)) if trends else None,
                       "trend_n": len(trends)})
    return result


def _color(value, scale, *, diverging=False, end=_PRICE_END):
    if value is None:
        return _MISSING_COLOR
    ratio = min(1, max(0, abs(value) / scale)) if scale > 0 else 0
    start, end = ((242, 246, 249), (57, 106, 173) if value < 0 else (180, 69, 61)) if diverging else ((232, 247, 243), end)
    return "#" + "".join(f"{round(a + (b - a) * ratio):02x}" for a, b in zip(start, end))


def _luminance(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _cell_style(background):
    """Background plus the black or white text that contrasts more with it (at least 4.58:1 for any color)."""
    luminance = _luminance(background)
    text = "#000000" if (luminance + 0.05) / 0.05 >= 1.05 / (luminance + 0.05) else "#ffffff"
    return f"background:{background};color:{text}"


def _scales(rows):
    return {key: max((abs(row[key]) for row in rows if row[key] is not None), default=0)
            for key in ("price", "trend", "jeonse")}


def _price_svg(rows, scale):
    width, left, right, row_height = 760, 95, 145, 27
    chart_width, top = width - left - right, 44
    height = top + len(rows) * row_height + 40
    elements = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
                'aria-labelledby="market-price-title market-price-desc">'
                '<title id="market-price-title">25개 구 관측 단지 가격 중위 비교</title>'
                '<desc id="market-price-desc">동일한 0 기점 억원 축. 각 구의 게이트 통과 단지 중위의 중위이며 표본 단지 수를 병기합니다.</desc>']
    for fraction in (0, .25, .5, .75, 1):
        x = left + chart_width * fraction
        elements.append(f'<line x1="{x:g}" y1="{top - 10}" x2="{x:g}" y2="{height - 30}" stroke="#e0e8e8"/>'
                        f'<text x="{x:g}" y="20" font-size="12" fill="#526473" text-anchor="middle">{scale * fraction:.1f}</text>')
    elements.append(f'<text x="{width - 5}" y="20" font-size="12" text-anchor="end">억원</text>')
    for index, row in enumerate(rows):
        y = top + index * row_height
        value = row["price"]
        elements.append(f'<text x="82" y="{y + 14}" font-size="14" text-anchor="end">{_escape(row["gu"])}</text>')
        if value is not None:
            bar_width = chart_width * (value / scale) if scale else 0
            elements.append(f'<rect data-district="{_escape(row["gu"])}" data-metric="price-bar" '
                            f'data-value="{_fmt(value)}" data-sample-n="{row["price_n"]}" '
                            f'x="{left}" y="{y}" width="{bar_width:.3f}" height="19" fill="#378e84"/>')
        elements.append(f'<text x="{width - right + 8}" y="{y + 14}" font-size="13">'
                        f'{_fmt(value)}{("억 · n=" + str(row["price_n"])) if value is not None else " · 표본 없음"}</text>')
    return "".join(elements) + "</svg>"


def _heatmap(rows, scales):
    body = []
    for row in rows:
        cells = [f'<th scope="row">{_escape(row["gu"])}</th>']
        for metric, unit in (("price", "억"), ("trend", "%"), ("jeonse", "%")):
            value = row[metric]
            attrs = (f'data-district="{_escape(row["gu"])}" data-metric="{metric}" '
                     f'data-state="{"observed" if value is not None else "missing"}" '
                     f'data-sample-n="{row[metric + "_n"]}" data-scale-max="{_fmt(scales[metric])}"')
            if value is not None:
                attrs += f' data-value="{_fmt(value)}"'
            label = f'{_fmt(value)}{unit} · n={row[metric + "_n"]}' if value is not None else '— · 표본 없음'
            cells.append(f'<td {attrs} style="{_cell_style(_color(value, scales[metric], diverging=metric == "trend"))}">{label}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return ('<div class="market-scroll" tabindex="0" aria-label="지역별 관측 히트맵. 좁은 화면에서는 표 안에서 가로로 스크롤하세요.">'
            '<table class="market-heatmap"><caption>각 열은 별도 척도 · n은 집계에 포함된 단지 수</caption>'
            '<thead><tr><th scope="col">구</th><th scope="col">중위가격 · 억</th>'
            '<th scope="col">단지 추세 중위 · %</th><th scope="col">전세가율 중위 · %</th></tr></thead><tbody>'
            + "".join(body) + '</tbody></table></div>')


def _tistory(rows, scales, ds, asof):
    cells = []
    for row in rows:
        price = row["price"]
        cells.append(f'<td data-district="{_escape(row["gu"])}" style="background:{_color(price, scales["price"], end=_TISTORY_PRICE_END)};'
                     'padding:1px;font-size:12px">'
                     f'<b>{_escape(row["gu"])}</b><br>{_fmt(price)}{"억" if price is not None else ""}<br>{row["n"]}곳({row["n_judged"]})<br>'
                     f'▲{row["up"]}<br>▼{row["down"]}</td>')
    grid = "".join("<tr>" + "".join(cells[i:i + 5]) + "</tr>" for i in range(0, len(cells), 5))
    bands = {name: 0 for _, name in be.PRICE_SEGMENTS}
    for row in ds.get("complexes", []):
        value = _number(row.get("molit_recent_eok")) if isinstance(row, dict) else None
        if value is not None and value > 0:
            band = be.price_segment(value)
            if band in bands:
                bands[band] += 1
    largest = max(bands.values(), default=0)
    bar_rows = "".join(f'<tr><td>{_escape(label)}</td><td><span style="color:#267b72">'
                       f'{"▰" * max(1, round(n / largest * 10)) if n and largest else "—"}</span> {n}곳</td></tr>'
                       for label, n in bands.items())
    return ('<h3>서울 25개 구 요약 · 가격 히트맵</h3>'
            '<table data-market-visuals="districts" style="width:100%;table-layout:fixed;font-size:12px">'
            + grid + '</table>'
            f'<p>기준일 {asof} · 색 밝음→진함 0→{_fmt(scales["price"])}억 · —=표본 없음 · '
            '값=게이트 통과 단지 중위의 중위 · 곳=관측, 괄호=추세 비교 단지(▲▼는 그중).</p>'
            '<h3>가격대 분포 · 관측 단지 수</h3><table style="width:100%;font-size:12px">'
            + bar_rows + '</table><p>국토교통부 RTMS · 가격 집계 아파트·40㎡+·매매10건+ · 가격대=관측단지 유효 중위.</p>')


def build_market_visuals(ds: dict, today: str) -> dict[str, str]:
    """25-district summaries and independently scaled price/trend/jeonse views."""
    current = _day(today)
    observed = _day(ds.get("data_asof")) if isinstance(ds, dict) else None
    if current is None:
        raise ValueError("market visuals date is invalid")
    if observed is None or observed > current:
        unavailable = '<p>지역별 시각화 · 자료 확인 필요 — 기준일을 확인하지 못했습니다.</p>'
        return {"site_html": unavailable, "tistory_html": unavailable}
    rows = _district_rows(ds)
    scales = _scales(rows)
    empty = '<p class="market-note">표본 없음 · 유효한 가격 관측을 확인하지 못했습니다.</p>' if not scales["price"] else ""
    site = (f'<section class="market-visuals" data-market-visuals="districts"><style>{_CSS}</style>'
            '<h2>구별 가격 비교</h2>' + empty
            + f'<p class="market-note">기준일 {observed.isoformat()} · 25구 · 국토교통부 RTMS(MOLIT). '
            '구별 값은 게이트 통과 단지 중위의 중위(억원)입니다. n은 가격 집계 단지 수이며 구 전체 거래량이 아닙니다.</p>'
            '<div class="market-scroll" tabindex="0" aria-label="25개 구 가격 막대 비교. 좁은 화면에서는 그래프 안에서 가로로 스크롤하세요.">'
            + _price_svg(rows, scales["price"]) + '</div><h2>지역별 관측 히트맵</h2>'
            + f'<div class="market-legend"><span>가격 0~{_fmt(scales["price"])}억 · 녹청</span>'
            f'<span>단지 추세 ±{_fmt(scales["trend"])}% · 상승 적색/하락 청색</span>'
            f'<span>전세가율 0~{_fmt(scales["jeonse"])}% · 녹청</span><span>회색 — · 표본 없음</span></div>'
            + _heatmap(rows, scales)
            + '<p class="market-note">가격: 아파트·전용 40㎡+·매매 10건+. 전세가율: 추가로 전세 5건+·95% 이하. '
            '추세: 최근 3개월과 직전 9개월의 표본 각각 2건+인 단지 추세의 중위. 각 열은 독립 공통 척도이며 미래 전망을 나타내지 않습니다. '
            '서로 다른 대표면적 단지를 비교하므로 개별 주택가격 비교와 다릅니다. '
            '<a href="https://rt.molit.go.kr/">국토교통부 실거래 원출처</a></p></section>')
    tistory = _tistory(rows, scales, ds, observed.isoformat())
    if not scales["price"]:
        tistory += '<p>표본 없음 · 가격 관측을 확인하지 못했습니다.</p>'
    return {"site_html": site, "tistory_html": tistory}


def _validated_series(code, ind, current, collected):
    from blog.macro_context import _usable

    if code not in _MACRO_RULES or not isinstance(ind, dict) or ind.get("code") != code:
        return None
    unit, freq = _MACRO_RULES[code]
    actual_unit = ind.get("unit")
    index_unit = isinstance(actual_unit, str) and re.fullmatch(r"\d{4}\.(?:0[1-9]|1[0-2])=100", actual_unit)
    if ind.get("freq") != freq or (not index_unit if unit == "index" else actual_unit != unit):
        return None
    if unit == "index":
        base = _day(actual_unit[:7].replace(".", "-") + "-01")
        if base is None or base > current:
            return None
    kind = "changes" if freq == "event" else "observations"
    if ind.get("series_kind") != kind or not _usable(ind, current):
        return None
    series = ind.get("series")
    if not isinstance(series, list) or not series:
        return None
    validated = []
    for item in series:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        day, value = _day(item[0]), _number(item[1])
        if day is None or day > collected or value is None or (validated and day <= validated[-1][0]):
            return None
        if unit in {"index", "원", "원/g", "$/oz", "호", "십억원", "십억달러"} and value < 0:
            return None
        validated.append((day, value))
    if validated[-1][0].isoformat() != ind.get("date") or validated[-1][1] != ind.get("value"):
        return None
    start = current - timedelta(days=365)
    kept = [(day, value) for day, value in validated if day >= start]
    anchor = None
    if kind == "changes":
        previous = [(day, value) for day, value in validated if day < start]
        if previous:
            anchor = (start, previous[-1][1], previous[-1][0])
    return {"points": kept, "anchor": anchor, "kind": kind, "freq": freq} if kept or anchor else None


def _gap(left, right, freq):
    days = (right - left).days
    if freq == "monthly":
        return right.year * 12 + right.month - left.year * 12 - left.month > 1
    return days > (14 if freq == "weekly" else 7)


def _macro_svg(code, ind, series, current):
    pts, anchor, kind = series["points"], series["anchor"], series["kind"]
    rendered = [(anchor[0], anchor[1])] + pts if anchor else list(pts)
    start = rendered[0][0]
    end = current if kind == "changes" else rendered[-1][0]
    span = max(1, (end - start).days)
    values = [value for _, value in rendered]
    actual_low, actual_high = min(values), max(values)
    divisor = max(abs(actual_low), abs(actual_high), 1)
    low, high = actual_low / divisor, actual_high / divisor
    padding = (high - low) * .12 if high != low else .05
    low, high = low - padding, high + padding
    left, right, top, bottom = 58, 520, 28, 170

    def x(day):
        return left + (day - start).days / span * (right - left)

    def y(value):
        return bottom - (value / divisor - low) / (high - low) * (bottom - top)

    elements = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 560 215" role="img" '
                f'aria-labelledby="market-{code}-title market-{code}-desc" data-code="{code}" data-unit="{_escape(ind["unit"])}">'
                f'<title id="market-{code}-title">{_escape(ind.get("label", code))} · {_escape(ind["unit"])}</title>'
                f'<desc id="market-{code}-desc">{start.isoformat()}부터 {end.isoformat()}까지의 실제 관측. '
                '날짜 간격을 그대로 사용하며 원값과 출처는 아래 관측목록에 표시합니다.</desc>']
    for value in (actual_low, actual_low / 2 + actual_high / 2, actual_high):
        yy = y(value)
        tick = f"{value:.3g}" if abs(value) >= 10000 else f"{value:.2f}"
        elements.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{right}" y2="{yy:.2f}" stroke="#e0e8e8"/>'
                        f'<text x="{left - 8}" y="{yy + 4:.2f}" font-size="12" text-anchor="end">{tick}</text>')
    if kind == "changes":
        path = f'M{x(rendered[0][0]):.3f},{y(rendered[0][1]):.3f}'
        for day, value in rendered[1:]:
            path += f'H{x(day):.3f}V{y(value):.3f}'
        path += f'H{x(end):.3f}'
        elements.append(f'<path d="{path}" data-step="true" fill="none" stroke="#267b72" stroke-width="2"/>')
    else:
        segments = 0
        for (day0, val0), (day1, val1) in zip(pts, pts[1:]):
            if not _gap(day0, day1, series["freq"]):
                segments += 1
                elements.append(f'<path d="M{x(day0):.3f},{y(val0):.3f}L{x(day1):.3f},{y(val1):.3f}" '
                                'fill="none" stroke="#267b72" stroke-width="2"/>')
        elements.append(f'<g data-line-segments="{segments}"/>')
    if anchor:
        elements.append(f'<circle cx="{x(anchor[0]):.3f}" cy="{y(anchor[1]):.3f}" r="3" fill="#fff" '
                        f'stroke="#267b72" data-anchor="true" data-source-date="{anchor[2].isoformat()}"/>')
    for day, value in pts:
        elements.append(f'<circle cx="{x(day):.3f}" cy="{y(value):.3f}" r="3" fill="#267b72" '
                        f'data-observation="true" data-date="{day.isoformat()}" data-value="{_fmt(value)}"/>')
    elements.append(f'<text x="{left}" y="200" font-size="12">{start.isoformat()}</text>'
                    f'<text x="{right}" y="200" font-size="12" text-anchor="end">{end.isoformat()}</text></svg>')
    return "".join(elements)


def render_macro_trends(snapshot: dict | None, today: str) -> str:
    """Individual-unit charts within the last 365 days, with exact observation lists."""
    from blog.macro_context import MAX_SNAPSHOT_AGE_DAYS

    current = _day(today)
    if current is None:
        raise ValueError("macro chart date is invalid")
    collected = _day((snapshot or {}).get("asof"))
    if collected is None or collected > current or (current - collected).days > MAX_SNAPSHOT_AGE_DAYS:
        return '<section class="market-visuals" data-market-visuals="macro-trends"><h2>거시 지표 관측 추이</h2><p>자료 미수집 · 최신 수집 기준일을 확인하지 못했습니다.</p></section>'
    indicators = snapshot.get("indicators")
    if not isinstance(indicators, dict):
        indicators = {}
    figures = []
    for code, ind in indicators.items():
        series = _validated_series(code, ind, current, collected)
        label = _escape(ind.get("label", code)) if isinstance(ind, dict) else _escape(code)
        if series is None:
            figures.append(f'<article class="market-series"><h3>{label}</h3><p>자료 확인 필요 · 날짜·단위·표본을 검증하지 못했습니다.</p></article>')
            continue
        points = series["points"]
        source = _escape(ind.get("source", "출처 미확인"))
        url = ind.get("url")
        try:
            parsed_url = urlsplit(url) if isinstance(url, str) else None
            safe_url = bool(parsed_url and parsed_url.scheme == "https" and parsed_url.hostname
                            and not parsed_url.username and not parsed_url.password)
        except ValueError:
            safe_url = False
        if safe_url:
            source = f'<a class="market-source" href="{_escape(url)}">{source}</a>'
        note = ('계단: 변경일 사이의 유효값을 유지합니다.' if series["kind"] == "changes"
                else '실제 관측만 표시하며 결측 구간은 연결하지 않습니다.')
        if code.startswith("cofix_"):
            note += ' 날짜는 대상월이 아니라 공시일입니다.'
        if code == "seoul_apt_sales":
            note += ' 거래량은 신고월 기준 동(호)수입니다.'
        if ind["freq"] == "monthly" and not code.startswith("cofix_"):
            note += ' 날짜의 1일은 기준월을 나타냅니다.'
        observation_rows = "".join(f'<li>{day.isoformat()} · {_fmt(value)} {_escape(ind["unit"])}</li>' for day, value in points)
        if series["anchor"]:
            anchor = series["anchor"]
            observation_rows = (f'<li>범위 시작 유효값: {_fmt(anchor[1])} {_escape(ind["unit"])} '
                                f'(원 변경일 {anchor[2].isoformat()})</li>') + observation_rows
        figures.append(f'<article class="market-series"><h3>{label}</h3><p class="market-note">'
                       f'{len(points)}개 관측 · 기준일 {_escape(ind["date"])} · 단위 {_escape(ind["unit"])} · {source}</p>'
                       '<div class="market-scroll" tabindex="0" aria-label="관측 시계열. 좁은 화면에서는 그래프 안에서 가로로 스크롤하세요.">'
                       + _macro_svg(code, ind, series, current)
                       + f'</div><p class="market-note">{note}</p><details><summary>관측값·기준일 전체 보기</summary>'
                       f'<ul>{observation_rows}</ul></details></article>')
    return (f'<section class="market-visuals" data-market-visuals="macro-trends"><style>{_CSS}</style>'
            '<h2>거시 지표 관측 추이</h2><p class="market-note">최근 365일 안의 확보된 관측기간만 표시합니다. '
            '그래프마다 단위와 세로축 범위가 다릅니다. 확보하지 못한 과거 값은 만들지 않습니다.</p>'
            '<div class="market-trends">' + ("".join(figures) or '<p>자료 미수집 · 관측값 없음</p>') + '</div></section>')
