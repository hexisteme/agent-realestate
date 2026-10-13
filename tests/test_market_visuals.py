from __future__ import annotations

from copy import deepcopy
from html.parser import HTMLParser
import importlib
import importlib.util
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

import pytest

import blog.build_explorer as be


TODAY = "2026-10-03"


def _api():
    assert importlib.util.find_spec("blog.market_visuals") is not None, "market visuals API is missing"
    return importlib.import_module("blog.market_visuals")


def _row(gu="강남", **changes):
    return {"gu": gu, "complex_no": gu + "-1", "name": "관측 단지", "product_type": "아파트",
            "area_m2": 84, "molit_n": 10, "molit_recent_eok": 10.0,
            "molit_trend_pct": 5.0, "molit_trend_dir": "▲", "molit_trend_n_recent": 2,
            "molit_trend_n_prior": 8, "jeonse_n": 5, "jeonse_ratio_complex_pct": 60.0,
            "trade_annual": 12, **changes}


def _dataset(rows):
    return {"generated": TODAY, "data_asof": TODAY, "count": len(rows), "complexes": rows}


class _HTML(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.elements = []
        self.text = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def _cell(source, gu, metric):
    return next(attrs for _, attrs in _HTML(source).elements
                if attrs.get("data-district") == gu and attrs.get("data-metric") == metric)


def _macro(code="kr_mortgage_new", unit="%", series=None, **changes):
    series = series or [["2026-07-01", 4.1], ["2026-08-01", 4.2]]
    ind = {"code": code, "label": "주택담보대출 금리", "unit": unit, "freq": "monthly",
           "series_kind": "observations", "date": series[-1][0], "value": series[-1][1],
           "series": series, "source": "한국은행 ECOS", "url": "https://ecos.bok.or.kr/", **changes}
    return {"asof": TODAY, "indicators": {code: ind}}


def _svgs(source):
    return [ET.fromstring(svg) for svg in re.findall(r"<svg\b.*?</svg>", source, flags=re.S)]


def test_district_views_preserve_original_summary_and_use_shared_price_gate():
    rows = [_row(), _row(complex_no="강남-2", molit_recent_eok=20, molit_trend_pct=-3,
                        molit_trend_dir="▼", jeonse_ratio_complex_pct=80),
            _row(complex_no="강남-3", product_type="도생", molit_recent_eok=2,
                 molit_trend_pct=99, molit_trend_dir="▲")]
    result = _api().build_market_visuals(_dataset(rows), TODAY)
    assert set(result) == {"site_html", "tistory_html"}
    cell = _cell(result["site_html"], "강남", "price")
    assert float(cell["data-value"]) == be.compute_gu_median(rows) == 15
    assert cell["data-sample-n"] == "2"
    assert "구별 가격 비교" in result["site_html"]
    assert "지역별 관측 히트맵" in result["site_html"]
    assert 'data-market-visuals="districts"' in result["site_html"]
    text = "".join(_HTML(result["tistory_html"]).text)
    assert "서울 25개 구 요약" in text and "가격 히트맵" in text
    # ▲·▼는 표본기준추세수(외부 리뷰 2026-10-13 C1) — 도생 ▲는 게이트 밖이라 세지 않는다.
    assert "15억" in text and "3곳" in text and "▲1▼1" in text
    assert sum(1 for _, a in _HTML(result["tistory_html"]).elements if a.get("data-district")) == 25


def test_district_heatmap_uses_per_column_scales_and_sample_counts():
    rows = [_row(), _row(complex_no="강남-2", molit_recent_eok=20, molit_trend_pct=-3,
                        jeonse_ratio_complex_pct=80),
            _row("서초", molit_recent_eok=30, molit_trend_pct=0)]
    source = _api().build_market_visuals(_dataset(rows), TODAY)["site_html"]
    assert float(_cell(source, "강남", "trend")["data-value"]) == 1
    assert float(_cell(source, "강남", "jeonse")["data-value"]) == 70
    assert _cell(source, "서초", "trend")["data-value"] == "0"
    assert _cell(source, "강남", "price")["data-scale-max"] == _cell(source, "서초", "price")["data-scale-max"]
    assert _cell(source, "강남", "jeonse")["data-scale-max"] != _cell(source, "강남", "price")["data-scale-max"]
    assert "단지 추세의 중위" in source and "최근 3개월" in source and "직전 9개월" in source


@pytest.mark.parametrize("value", [None, True, -1, 0, float("nan"), float("inf"), "10"])
def test_invalid_price_is_missing_in_both_surfaces(value):
    result = _api().build_market_visuals(_dataset([_row(molit_recent_eok=value)]), TODAY)
    cell = _cell(result["site_html"], "강남", "price")
    assert cell["data-state"] == "missing" and "data-value" not in cell
    assert "—" in result["tistory_html"]
    assert not re.search(r'="(?:nan|inf|True)"', result["site_html"])


def test_trend_and_jeonse_require_actual_sample_gates():
    rows = [_row(molit_trend_n_recent=1, jeonse_n=4),
            _row("서초", molit_trend_n_recent=2, molit_trend_n_prior=2, molit_trend_pct=0,
                 jeonse_ratio_complex_pct=96)]
    source = _api().build_market_visuals(_dataset(rows), TODAY)["site_html"]
    assert _cell(source, "강남", "trend")["data-state"] == "missing"
    assert _cell(source, "강남", "jeonse")["data-state"] == "missing"
    assert _cell(source, "서초", "trend")["data-value"] == "0"
    assert _cell(source, "서초", "jeonse")["data-state"] == "missing"


def test_empty_dataset_has_missing_districts_and_no_invented_price():
    result = _api().build_market_visuals(_dataset([]), TODAY)
    assert "표본 없음" in result["site_html"] and "표본 없음" in result["tistory_html"]
    for _, attrs in _HTML(result["site_html"]).elements:
        if attrs.get("data-metric") == "price":
            assert attrs["data-state"] == "missing" and "data-value" not in attrs


def test_price_bars_share_zero_baseline_and_svg_has_textual_accessibility():
    source = _api().build_market_visuals(_dataset([_row(), _row("서초", molit_recent_eok=20)]), TODAY)["site_html"]
    svgs = _svgs(source)
    assert svgs
    svg = svgs[0]
    assert svg.find("{*}title") is not None and svg.find("{*}desc") is not None
    bars = [element for element in svg.iter() if element.get("data-metric") == "price-bar"]
    assert len(bars) == 2
    assert bars[0].get("x") == bars[1].get("x")
    widths = sorted(float(element.get("width")) for element in bars)
    assert widths[1] == pytest.approx(2 * widths[0], abs=.02)
    assert "MOLIT" in source and "억원" in source and "n=1" in source
    assert "<script" not in source and "javascript:" not in source and "<foreignObject" not in source


def test_tistory_is_safe_compact_and_keeps_twenty_five_districts():
    rows = [_row(gu, molit_recent_eok=(i + 1) / 2) for i, gu in enumerate(sorted(be.GU_LAWD))]
    html = _api().build_market_visuals(_dataset(rows), TODAY)["tistory_html"]
    parsed = _HTML(html)
    assert {tag for tag, _ in parsed.elements} <= {"table", "tr", "td", "b", "p", "span", "a", "br", "h3"}  # h3·h4: 본문 절 제목(스킨 글 제목 h2 아래, design-review 2026-10-09 floker-114 B-3)
    assert parsed.elements[1][0] in {"b", "span", "table"}
    tables = [attrs for tag, attrs in parsed.elements if tag == "table"]
    assert tables[0]["data-market-visuals"] == "districts"
    assert len(html.encode()) <= 4600  # R3 F7: 구 칸 추세 분모 '(n)' ≈100B 추가. 본문 전체 예산은 daily_digest 축약 루프가 집행
    assert "가격대 분포" in html
    assert len([a for _, a in parsed.elements if "data-district" in a]) == 25


def test_malicious_labels_are_not_injected_and_input_is_not_mutated():
    data = _dataset([_row(name='</td><script>alert(1)</script>')])
    before = deepcopy(data)
    result = _api().build_market_visuals(data, TODAY)
    assert data == before and "<script" not in result["site_html"]
    snap = _macro(label='<script>alert(1)</script>')
    before = deepcopy(snap)
    source = _api().render_macro_trends(snap, TODAY)
    assert snap == before and "<script>" not in source
    assert "&lt;script&gt;" in source


def test_macro_uses_real_date_spacing_exact_values_units_and_short_history():
    series = [["2026-07-01", 4.1], ["2026-07-11", 4.2], ["2026-08-10", 4.3]]
    source = _api().render_macro_trends(_macro(series=series), TODAY)
    svg = _svgs(source)[0]
    points = [e for e in svg.iter() if e.get("data-observation") == "true"]
    assert [e.get("data-date") for e in points] == [s[0] for s in series]
    assert [float(e.get("data-value")) for e in points] == [s[1] for s in series]
    xs = [float(e.get("cx")) for e in points]
    assert xs[2] - xs[1] == pytest.approx(3 * (xs[1] - xs[0]), abs=.1)
    assert "2026-07-01" in source and "2026-08-10" in source and "한국은행 ECOS" in source
    assert "data-unit=\"%\"" in source and "3点" not in source


@pytest.mark.parametrize("series", [
    [["2026-07-01", 4], ["2026-07-01", 5]],
    [["2026-07-02", 4], ["2026-07-01", 5]],
    [["2026-02-30", 4], ["2026-08-01", 5]],
    [["2026-08-01", True], ["2026-09-01", 5]],
    [["2026-08-01", float("nan")], ["2026-09-01", 5]],
    [["2026-08-01", 4], ["2026-10-04", 5]],
])
def test_malformed_series_is_explicitly_unavailable(series):
    source = _api().render_macro_trends(_macro(series=series), TODAY)
    assert not _svgs(source) and "자료 확인 필요" in source


@pytest.mark.parametrize("changes", [{"unit": "원"}, {"date": "2026-10-04"}, {"value": 9}, {"freq": "daily"}])
def test_wrong_macro_contract_cannot_render_valid_looking_chart(changes):
    snap = _macro(**changes)
    source = _api().render_macro_trends(snap, TODAY)
    assert not _svgs(source) and "자료 확인 필요" in source


def test_macro_stale_snapshot_and_stale_observation_do_not_render():
    snap = _macro()
    snap["asof"] = "2026-09-29"
    assert not _svgs(_api().render_macro_trends(snap, TODAY))
    assert not _svgs(_api().render_macro_trends(_macro(series=[["2026-01-01", 4]]), TODAY))


def test_macro_monthly_missing_month_breaks_line_instead_of_interpolating():
    snap = _macro(series=[["2026-06-01", 4.1], ["2026-08-01", 4.2]])
    source = _api().render_macro_trends(snap, TODAY)
    assert "결측 구간" in source
    assert 'data-line-segments="0"' in source
    assert source.count('data-observation="true"') == 2


def test_policy_changes_step_chart_includes_valid_prior_anchor():
    snap = _macro(code="bok_base", unit="%", freq="event", series_kind="changes",
                  series=[["2020-01-01", 2.0], ["2026-08-27", 2.5]])
    source = _api().render_macro_trends(snap, TODAY)
    assert "계단" in source and "유효값" in source
    assert 'data-step="true"' in source and 'data-anchor="true"' in source
    assert source.count('data-observation="true"') == 1


def test_three_month_housing_volume_retains_only_observed_history():
    snap = _macro(code="seoul_apt_sales", unit="호",
                  series=[["2026-06-01", 2000], ["2026-07-01", 2100], ["2026-08-01", 2200]])
    source = _api().render_macro_trends(snap, TODAY)
    assert source.count('data-observation="true"') == 3
    assert "3개 관측" in source and "2026-06-01" in source and "신고월" in source


def test_price_index_reference_base_is_visible_and_values_are_not_rates():
    snap = _macro(code="seoul_apt_sale", unit="2026.06=100",
                  series=[["2026-07-01", 100.5], ["2026-08-01", 101]])
    source = _api().render_macro_trends(snap, TODAY)
    assert 'data-unit="2026.06=100"' in source and "2026.06=100" in source
    assert "%p" not in source


def test_actual_public_dataset_tistory_budget_and_all_summaries():
    dataset_path = Path(__file__).parents[1] / "report/blog/dataset.json"
    if not dataset_path.exists():
        pytest.skip("operational public dataset is not available in this checkout")
    ds = json.loads(dataset_path.read_text())
    result = _api().build_market_visuals(ds, ds["generated"])
    assert len(result["tistory_html"].encode()) <= 4600  # R3 F7 분모 병기(위 주석)
    assert len([a for _, a in _HTML(result["tistory_html"]).elements if "data-district" in a]) == 25


def test_future_dataset_is_not_presented_as_current_observation():
    ds = _dataset([_row()])
    ds["data_asof"] = "2026-10-04"
    result = _api().build_market_visuals(ds, TODAY)
    assert "자료 확인 필요" in result["site_html"]
    assert not _svgs(result["site_html"])


def test_malformed_source_url_does_not_abort_or_inject_chart():
    source = _api().render_macro_trends(_macro(url="https://[bad-origin"), TODAY)
    assert _svgs(source)
    assert "bad-origin" not in source
    source = _api().render_macro_trends(_macro(url="javascript:alert(1)"), TODAY)
    assert _svgs(source) and "javascript:" not in source


def test_series_cannot_contain_observations_after_snapshot_asof():
    snap = _macro(series=[["2026-08-01", 4.1], ["2026-10-02", 4.2]])
    snap["asof"] = "2026-10-01"
    source = _api().render_macro_trends(snap, TODAY)
    assert not _svgs(source) and "자료 확인 필요" in source


@pytest.mark.parametrize("unit", ["2027.06=100", "0000.06=100"])
def test_future_or_invalid_price_index_base_is_unavailable(unit):
    snap = _macro(code="seoul_apt_sale", unit=unit)
    source = _api().render_macro_trends(snap, TODAY)
    assert not _svgs(source)


def test_finite_extreme_price_cannot_create_infinite_geometry():
    ds = _dataset([_row(molit_recent_eok=1e308), _row(complex_no="강남-2", molit_recent_eok=1e308)])
    result = _api().build_market_visuals(ds, TODAY)
    assert not re.search(r'="[^\"]*(?:inf|nan)[^\"]*"', result["site_html"])
    assert _cell(result["site_html"], "강남", "price")["data-state"] == "missing"


def test_actual_tistory_gu_counts_and_medians_match_existing_summary():
    from blog.daily_digest import _gu_summary_rows
    path = Path(__file__).parents[1] / "report/blog/dataset.json"
    if not path.exists():
        pytest.skip("operational public dataset is not available in this checkout")
    ds = json.loads(path.read_text())
    source = _api().build_market_visuals(ds, ds["generated"])["tistory_html"]
    cells = re.findall(r'<td data-district="([^"]+)"[^>]*>(.*?)</td>', source, flags=re.S)
    by = {gu: "".join(_HTML(body).text) for gu, body in cells}
    for row in _gu_summary_rows(ds):
        assert f'{_api()._fmt(row["gu_median"])}억' in by[row["gu"]]
        assert f'{row["n"]}곳' in by[row["gu"]]
        assert f'▲{row["up"]}▼{row["down"]}' in by[row["gu"]]


def test_all_valid_input_district_labels_are_preserved_and_escaped():
    rows = [_row(f"구{i:04d}") for i in range(200)] + [_row('<img src=x onerror="alert(1)">')]
    result = _api().build_market_visuals(_dataset(rows), TODAY)
    parsed = _HTML(result["tistory_html"])
    districts = [a["data-district"] for _, a in parsed.elements if a.get("data-district")]
    assert districts == sorted(set(be.GU_LAWD) | {r["gu"] for r in rows})
    assert len(districts) == 226
    assert all(tag != "img" for tag, _ in parsed.elements)
    assert "&lt;img" in result["tistory_html"]
    assert len(result["tistory_html"].encode()) > 4500


def test_partial_dataset_keeps_all_seoul_districts_as_explicit_missing():
    result = _api().build_market_visuals(_dataset([_row("강남")]), TODAY)
    districts = [a["data-district"] for _, a in _HTML(result["tistory_html"]).elements
                 if a.get("data-district")]
    assert districts == sorted(be.GU_LAWD)
    assert _cell(result["site_html"], "강남", "price")["data-value"] == "10"
    for metric in ("price", "trend", "jeonse"):
        cell = _cell(result["site_html"], "서초", metric)
        assert cell["data-state"] == "missing"
        assert cell["data-sample-n"] == "0"
        assert "data-value" not in cell
        assert "#e5e7eb" in cell["style"]


def test_svg_axis_fonts_keep_native_size_inside_scroll_and_tablet_grid():
    district = _api().build_market_visuals(_dataset([_row()]), TODAY)["site_html"]
    macro = _api().render_macro_trends(_macro(), TODAY)
    for source, selector in [(district, r"\.market-visuals svg"),
                             (macro, r"\.market-visuals \.market-series svg")]:
        rule = re.search(selector + r"\{([^}]+)\}", source).group(1)
        minimum_width = int(re.search(r"min-width:(\d+)px", rule).group(1))
        svg = _svgs(source)[0]
        viewbox_width = float(svg.get("viewBox").split()[2])
        font_sizes = [float(element.get("font-size")) for element in svg.iter() if element.get("font-size")]
        smallest_rendered_font = min(font_sizes) * minimum_width / viewbox_width
        assert smallest_rendered_font >= 12, "the SVG min-width must not shrink 12px axes inside the scroll container"
    assert re.search(r"@media\s*\(max-width:1100px\)\s*\{\.market-visuals \.market-trends\{grid-template-columns:1fr", macro)
    assert "grid-template-columns:repeat(auto-fit" in macro


def test_tistory_price_and_total_complex_count_have_separate_lines():
    source = _api().build_market_visuals(_dataset([_row()]), TODAY)["tistory_html"]
    body = re.search(r'<td data-district="강남"[^>]*>(.*?)</td>', source, re.S).group(1)
    assert "10억<br>1곳(1)<br>" in body
    assert "▲1<br>▼0" in body
    missing = re.search(r'<td data-district="서초"[^>]*>(.*?)</td>', source, re.S).group(1)
    assert "—<br>0곳(0)<br>" in missing


def test_tistory_heatmap_survives_skin_cell_and_paragraph_font_overrides():
    source = _api().build_market_visuals(_dataset([_row(molit_recent_eok=17.58)]), TODAY)["tistory_html"]
    cells = re.findall(r'<td data-district="[^"]+" style="([^"]+)">(.*?)</td>', source, re.S)
    assert len(cells) == 25
    for style, body in cells:
        # Tistory sets td=15px and p=16px even when table declares 12px.
        assert "font-size:12px" in style
        assert "<p" not in body
    assert "17.58억<br>1곳" in source
    assert "▲1<br>▼0" in source


def _contrast(foreground, background):
    def luminance(color):
        channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = (c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("surface", ["tistory_html", "site_html"])
def test_heatmap_cell_text_keeps_aa_contrast(surface):
    # The skin's #333333 text sank into dark price cells (서초 3.22:1, 강남 4.42:1 — design review B-1).
    # Tistory keeps only background-color on a published cell, so its text is the skin's #article-view color.
    rows = [_row(gu, molit_recent_eok=price, molit_trend_pct=trend, jeonse_ratio_complex_pct=ratio)
            for gu, price, trend, ratio in [("강남", 30.0, -12.0, 40.0), ("서초", 25.0, 12.0, 80.0),
                                            ("송파", 12.0, 0.4, 55.0), ("도봉", 3.0, -3.0, 70.0)]]
    source = _api().build_market_visuals(_dataset(rows), TODAY)[surface]
    styles = [attrs["style"] for tag, attrs in _HTML(source).elements if tag == "td" and attrs.get("data-district")]
    assert len(styles) == (25 if surface == "tistory_html" else 75)
    for style in styles:
        background = re.search(r"background:(#[0-9a-f]{6})", style).group(1)
        color = re.search(r"(?:^|;)color:(#[0-9a-f]{6})", style)
        if surface == "tistory_html":
            assert not color
            text = "#333333"
        else:
            assert color, f"site cell on {background} inherits the page text color"
            text = color.group(1)
        assert _contrast(text, background) >= 4.5, style
