"""시장흐름대시보드(MarketDashboard) — 공통 차트 모델·관측요약·읽는법·결정론 PNG."""
import re

import pytest

from agent_realestate.collectors import macro as collector
from blog import market_charts as mch
from blog.wording_guard import assert_wording_ok


def _months(start, n):
    y, m = map(int, start.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}-01")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _ind(code, unit, series, freq="monthly"):
    return collector.build_indicator(code, code, unit, freq, "src", "https://ecos.bok.or.kr/", series)


def snapshot(asof="2026-10-10"):
    months = _months("2024-01", 32)  # 2024-01 .. 2026-08
    sale = [(d, 90 + i * 0.4) for i, d in enumerate(months)]
    jeonse = [(d, 95 + i * 0.25) for i, d in enumerate(months)]
    trades = [(d, 4000 + 100 * i) for i, d in enumerate(months[-24:])]
    rate = [(d, 3.5 + i * 0.03) for i, d in enumerate(months)]
    return {"asof": asof, "indicators": {
        "seoul_apt_sale": _ind("seoul_apt_sale", "2026.06=100", sale),
        "seoul_apt_jeonse": _ind("seoul_apt_jeonse", "2026.06=100", jeonse),
        "seoul_apt_sales": _ind("seoul_apt_sales", "호", trades),
        "kr_mortgage_new": _ind("kr_mortgage_new", "%", rate),
        "bok_base": _ind("bok_base", "%", [("2024-10-11", 3.25), ("2025-05-29", 2.75), ("2026-08-27", 3.0)], "event"),
    }}


def test_dashboard_keeps_last_24_calendar_months_and_exact_month_deltas():
    d = mch.build_market_dashboard(snapshot(), "2026-10-10")
    assert [m for m, _ in d.sale.points][0] == "2024-09" and d.sale.points[-1][0] == "2026-08"
    assert len(d.sale.points) == 24 and len(d.trades.points) == 24
    last, prev = d.sale.points[-1][1], d.sale.points[-2][1]
    assert d.sale.mom == pytest.approx(round((last / prev - 1) * 100, 1))
    year_ago = dict(d.sale.points)["2025-08"]
    assert d.sale.yoy == pytest.approx(round((last / year_ago - 1) * 100, 1))
    assert d.mortgage.mom == pytest.approx(round(d.mortgage.points[-1][1] - d.mortgage.points[-2][1], 2))
    assert d.base_label == "2026년 6월"


def test_missing_previous_month_gives_no_delta_instead_of_skipping_back():
    snap = snapshot()
    series = [row for row in snap["indicators"]["seoul_apt_sale"]["series"] if row[0] != "2026-07-01"]
    snap["indicators"]["seoul_apt_sale"] = _ind("seoul_apt_sale", "2026.06=100", series)
    d = mch.build_market_dashboard(snap, "2026-10-10")
    assert d.sale.mom is None and d.sale.yoy is not None


def test_base_rate_steps_are_the_rate_in_force_at_each_month_end():
    d = mch.build_market_dashboard(snapshot(), "2026-10-10")
    base = dict(d.base_rate)
    assert base["2025-04"] == 3.25 and base["2025-05"] == 2.75 and base["2026-07"] == 2.75 and base["2026-08"] == 3.0
    assert d.spread == pytest.approx(round(d.mortgage.points[-1][1] - 3.0, 2))


@pytest.mark.parametrize("code,unit", [("seoul_apt_sale", "2021.06=100"), ("seoul_apt_sales", "%"),
                                       ("kr_mortgage_new", "호")])
def test_wrong_unit_drops_only_that_panel(code, unit):
    snap = snapshot()
    snap["indicators"][code] = _ind(code, unit, snap["indicators"][code]["series"])
    d = mch.build_market_dashboard(snap, "2026-10-10")
    attr = {"seoul_apt_sale": "sale", "seoul_apt_sales": "trades", "kr_mortgage_new": "mortgage"}[code]
    assert getattr(d, attr) is None
    assert d.jeonse is not None


def test_stale_or_future_observations_are_not_drawn():
    assert mch.build_market_dashboard(snapshot(asof="2026-09-01"), "2026-12-30") is None
    snap = snapshot()
    snap["indicators"]["seoul_apt_sales"] = _ind("seoul_apt_sales", "호",
                                                 [("2026-11-01", 5000), ("2026-08-01", 4589)])
    d = mch.build_market_dashboard(snap, "2026-10-10")
    assert d.trades is None


def test_table_rows_are_last_six_months_with_dash_for_gaps():
    snap = snapshot()
    snap["indicators"]["seoul_apt_sales"] = _ind("seoul_apt_sales", "호",
                                                 [r for r in snap["indicators"]["seoul_apt_sales"]["series"]
                                                  if r[0] != "2026-05-01"])
    rows = mch.dashboard_table_rows(mch.build_market_dashboard(snap, "2026-10-10"))
    assert [r[0] for r in rows] == ["2026-03", "2026-04", "2026-05", "2026-06", "2026-07", "2026-08"]
    assert rows[2][3] == "—" and re.fullmatch(r"\d{1,3}(,\d{3})*", rows[-1][3])


def test_reading_notes_state_meaning_and_formula_from_sources_only():
    notes = mch.reading_notes(mch.build_market_dashboard(snapshot(), "2026-10-10"))
    text = " ".join(" ".join(n) for n in notes)
    assert "2026년 6월=100" in text and "제본스" in text and "신고일 기준" in text and "가중평균" in text
    assert "동(호)수" not in text and "월말 확정" not in text   # 동 단위 오독·공표 시점 혼동(agy 2차)
    assert "기준금리" in text
    for title, meaning, formula in notes:
        assert len(meaning) <= 70 and len(formula) <= 90
    assert_wording_ok(text, "reading_notes")


def test_observation_summary_is_slot_template_without_judgement():
    d = mch.build_market_dashboard(snapshot(), "2026-10-10")
    counts = {"n_total": 1756, "n_judged": 482, "up": 460, "down": 20}
    s = mch.compose_observation_summary(counts, d, asof="2026-10-10")
    assert s.startswith("2026-10-10 관측")
    assert "1,756단지 중 3·9개월 비교가 가능한 482단지(관측의 27.4%)" in s and "높은 곳 460" in s and "낮은 곳 20" in s and "8월" in s
    for word in ("적기", "추천", "전망", "오를", "내릴", "기회", "매수", "매도", "바닥", "고점", "판정"):
        assert word not in s
    assert_wording_ok(s, "observation_summary")
    assert mch.compose_observation_summary({}, None, asof="2026-10-10") == ""


def test_dashboard_png_is_deterministic_and_sized_for_mobile(tmp_path):
    pytest.importorskip("PIL")
    d = mch.build_market_dashboard(snapshot(), "2026-10-10")
    a = mch.render_dashboard_png(d)
    b = mch.render_dashboard_png(d)
    assert a == b and a[:8] == b"\x89PNG\r\n\x1a\n"
    image = mch.write_dashboard_png(d, tmp_path)
    assert re.fullmatch(r"dashboard-2026-10-10-[0-9a-f]{16}\.png", image.name)
    assert image.width == mch.DASHBOARD_WIDTH and 900 <= image.height <= 1800  # 세로 3패널, 360px 화면에서 약 600px
    assert (tmp_path / image.name).read_bytes() == a


def test_dashboard_png_survives_missing_panels():
    pytest.importorskip("PIL")
    snap = snapshot()
    del snap["indicators"]["seoul_apt_sales"]
    d = mch.build_market_dashboard(snap, "2026-10-10")
    assert d.trades is None
    assert mch.render_dashboard_png(d)[:4] == b"\x89PNG"


def _drawn_texts(monkeypatch, d):
    pytest.importorskip("PIL")
    from PIL import ImageDraw
    seen = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        seen.append((text, kwargs.get("font")))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    mch.render_dashboard_png(d)
    return seen


def test_png_text_uses_only_glyphs_the_font_has(monkeypatch):
    snap = snapshot()
    series = snap["indicators"]["seoul_apt_sales"]["series"]
    snap["indicators"]["seoul_apt_sales"] = _ind("seoul_apt_sales", "호", series[:-1] + [(series[-1][0], 3000)])
    d = mch.build_market_dashboard(snap, "2026-10-10")
    assert d.trades.mom < 0
    seen = _drawn_texts(monkeypatch, d)
    for text, font in seen:
        notdef = bytes(font.getmask("￿"))
        missing = [ch for ch in set(text) if not ch.isspace() and bytes(font.getmask(ch)) == notdef]
        assert not missing, (text, missing)


def test_trades_header_omits_year_ago_when_unknown(monkeypatch):
    snap = snapshot()
    snap["indicators"]["seoul_apt_sales"] = _ind("seoul_apt_sales", "호",
                                                 snap["indicators"]["seoul_apt_sales"]["series"][-3:])
    d = mch.build_market_dashboard(snap, "2026-10-10")
    assert d.trades.yoy is None
    texts = [t for t, _ in _drawn_texts(monkeypatch, d)]
    assert not any("1년 전" in t for t in texts)


@pytest.mark.parametrize("lo,hi", [(85.3, 103.1), (0.0, 8671.0), (2.2, 4.9), (99.2, 100.4), (0.0, 7.0)])
def test_axis_ticks_are_round_and_cover_the_range(lo, hi):
    ticks = mch._nice_ticks(lo, hi)
    assert 3 <= len(ticks) <= 6 and ticks[0] <= lo and ticks[-1] >= hi
    step = ticks[1] - ticks[0]
    mantissa = step / 10 ** __import__("math").floor(__import__("math").log10(step))
    assert any(abs(mantissa - m) < 1e-9 for m in (1, 2, 2.5, 5))
    assert all(abs((b - a) - step) < 1e-9 for a, b in zip(ticks, ticks[1:]))


def test_x_labels_mark_januaries_clear_of_the_ends():
    months = tuple(f"{y}-{m:02d}" for y, m in [(2024, 9), (2024, 10), (2024, 11), (2024, 12)]
                   + [(2025, k) for k in range(1, 13)] + [(2026, k) for k in range(1, 9)])
    assert mch._x_labels(months) == ["2024-09", "2025-01", "2026-01", "2026-08"]
    assert mch._x_labels(months[2:]) == ["2024-11", "2026-01", "2026-08"]


TISTORY_PHONE_COLUMN = 310  # floker 스킨 본문 폭(390px 화면 실측) — 이미지가 이 폭으로 줄어든다


def test_png_text_fits_canvas_and_stays_legible_in_phone_column(monkeypatch):
    pytest.importorskip("PIL")
    from PIL import ImageDraw
    boxes = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        boxes.append((text, font.size, self.textbbox(xy, text, font=font, anchor=kwargs.get("anchor")), self.im.size))
        return original(self, xy, text, *args, **kwargs)

    snap = snapshot()
    series = snap["indicators"]["seoul_apt_sales"]["series"]
    snap["indicators"]["seoul_apt_sales"] = _ind("seoul_apt_sales", "호", series[:-1] + [(series[-1][0], 3000)])
    d = mch.build_market_dashboard(snap, "2026-10-10")
    assert d.trades.yoy is not None and d.spread is not None   # 가장 긴 머리줄(1년 전·기준금리 차이)까지 그린다
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    mch.render_dashboard_png(d)
    for text, size, (x0, y0, x1, y1), (width, height) in boxes:
        assert 0 <= x0 and x1 <= width and 0 <= y0 and y1 <= height, (text, (x0, x1), width)
        assert size / width * TISTORY_PHONE_COLUMN >= 10, (text, size / width * TISTORY_PHONE_COLUMN)


def test_flat_series_still_draws_with_at_least_three_ticks():
    pytest.importorskip("PIL")
    ticks = mch._nice_ticks(4.0, 4.0)
    assert len(ticks) >= 3 and ticks[0] < 4.0 < ticks[-1]
    snap = snapshot()
    snap["indicators"]["kr_mortgage_new"] = _ind("kr_mortgage_new", "%", [("2026-07-01", 4.0), ("2026-08-01", 4.0)])
    del snap["indicators"]["bok_base"]
    d = mch.build_market_dashboard(snap, "2026-10-10")
    assert d.mortgage and mch.render_dashboard_png(d)[:4] == b"\x89PNG"


def test_png_texts_never_overlap_each_other(monkeypatch):
    pytest.importorskip("PIL")
    from PIL import ImageDraw
    boxes = []
    original = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *args, **kwargs):
        boxes.append((text, self.textbbox(xy, text, font=kwargs.get("font"), anchor=kwargs.get("anchor"))))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    mch.render_dashboard_png(mch.build_market_dashboard(snapshot(), "2026-10-10"))
    for i, (a, (ax0, ay0, ax1, ay1)) in enumerate(boxes):
        for b, (bx0, by0, bx1, by1) in boxes[i + 1:]:
            assert ax1 <= bx0 or bx1 <= ax0 or ay1 <= by0 or by1 <= ay0, (a, b)
