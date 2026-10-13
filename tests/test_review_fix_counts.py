"""외부 리뷰(2026-10-13) C1·C3·C5 — 상승·하락 집계 단일 기준과 관측 깔때기.

제목·썸네일·구 요약·주간 요약·구 허브가 게이트 없는 원시 ▲·▼(1,755단지 중 649)를 세고,
본문 관측요약은 표본기준추세수(486단지 중 464)를 세어 같은 글에 다른 상승 수가 실렸다.
모든 공개 표면이 build_explorer.count_gated_trends 하나를 쓰는지 고정한다.
"""
from blog import build_explorer as be


def _row(name, *, kind="아파트", area=59.0, n=15, price=10.0, trend=None, pct=None,
         n_recent=None, n_prior=None, gu="강남"):
    return dict(gu=gu, name=name, product_type=kind, area_m2=area, molit_n=n,
                molit_recent_eok=price, molit_trend_dir=trend, molit_trend_pct=pct,
                molit_trend_n_recent=n_recent, molit_trend_n_prior=n_prior)


def _rows():
    return [
        _row("통과상승5건", trend="▲", pct=5.0, n_recent=5, n_prior=10),
        _row("통과상승2건", trend="▲", pct=9.0, n_recent=2, n_prior=13),
        _row("통과하락6건", trend="▼", pct=-2.0, n_recent=6, n_prior=9),
        _row("통과추세없음", trend=None, pct=None, n_recent=1, n_prior=14),
        _row("주상복합상승", kind="주상복합", trend="▲", pct=4.0, n_recent=3, n_prior=8),
        _row("소형상승", area=35.0, trend="▲", pct=3.0, n_recent=4, n_prior=7),
        _row("표본부족상승", n=5, trend="▲", pct=1.0, n_recent=2, n_prior=3),
        _row("가격없음", n=None, price=None),
    ]


def test_count_gated_trends_excludes_ungated_rows_and_reports_robust_subset():
    c = be.count_gated_trends(_rows())
    assert (c["n_judged"], c["up"], c["down"]) == (3, 2, 1)
    # 보조 집계: 최근 3개월 표본 5건 이상만
    assert (c["n_robust"], c["up_robust"], c["down_robust"]) == (2, 1, 1)
    assert c["robust_min_recent"] == 5


def test_observation_funnel_partitions_every_row_once():
    f = be.compute_observation_funnel(_rows())
    assert f["n_total"] == 8
    assert f["n_priced"] == 7 and f["n_no_price"] == 1
    assert (f["excl_non_apt"], f["excl_small"], f["excl_thin"]) == (1, 1, 1)
    assert f["n_gated"] == 4
    assert f["n_judged"] == 3 and f["n_unjudged"] == 1
    assert f["n_priced"] == f["n_gated"] + f["excl_non_apt"] + f["excl_small"] + f["excl_thin"]
    assert f["n_total"] == f["n_priced"] + f["n_no_price"]


def test_visual_first_reuses_the_canonical_counter():
    from blog import visual_first as vf
    assert vf.count_gated_trends is be.count_gated_trends


def test_daily_counts_title_and_gu_rows_use_gated_trends():
    from blog import daily_digest as dd
    ds = {"complexes": _rows(), "count": 8, "data_asof": "2026-10-12", "generated": "2026-10-13"}
    counts = dd._today_counts(ds)
    assert (counts["n_judged"], counts["up"], counts["down"]) == (3, 2, 1)
    assert counts["n_total"] == 8
    gu = dd._gu_summary_rows(ds)
    assert [(g["up"], g["down"]) for g in gu] == [(2, 1)]


def test_thumbnail_card_counts_match_gated_trends():
    from blog import thumbnail_card as card
    ds = {"complexes": _rows(), "count": 8, "data_asof": "2026-10-12", "generated": "2026-10-13"}
    data = card.prepare_daily_card(ds)
    assert (data.up_count, data.down_count) == (2, 1)
    assert data.judged_count == 3


def test_weekly_summary_and_gu_hub_use_gated_trends():
    from blog import weekly_summary as ws
    ds = {"complexes": _rows(), "count": 8, "data_asof": "2026-10-12", "generated": "2026-10-13"}
    text = ws.build_weekly_summary(ds, "2026-10-13")
    assert "▲2" in text and "▼1" in text
    assert "▲5" not in text


def test_market_visuals_district_rows_use_gated_trends():
    from blog import market_visuals as mv
    ds = {"complexes": _rows(), "count": 8}
    row = next(r for r in mv._district_rows(ds) if r["gu"] == "강남")
    assert (row["up"], row["down"]) == (2, 1)


def _bigdeal():
    return dict(gu="관악", name="벽산블루밍", product_type="아파트", area_m2=60.0, molit_n=75,
                molit_recent_eok=9.55, trade_annual=154.0, units=177, turnover_pct=87.0,
                molit_pos_52w=93, molit_trend_dir="▲", molit_trend_pct=15.6,
                molit_trend_n_recent=6, molit_trend_n_prior=69)


def test_turnover_text_separates_trade_count_units_and_price_sample():
    """C6 — 회전율 분자(전체면적 12개월 거래)·분모(세대수)·가격 표본(대표면적)을 따로 적는다."""
    from blog import daily_digest as dd
    txt = dd._turnover_txt(_bigdeal())
    assert "회전율 87%" in txt
    assert "154건" in txt and "177세대" in txt
    assert "가격 표본 75건" in txt and "60㎡" in txt
    sel = {"tv": [_bigdeal()], "lo": [], "hi": [], "jr": []}
    html = dd._render_ranked_summary(sel, compact=True, level=3)
    assert "12개월 표본 75건" not in html
    assert "154건" in html and "177세대" in html


def test_band_rows_state_the_median_population_next_to_the_complex_count():
    """Codex X1 — 가격대 표의 단지 수(전체)와 중위(집계 기준 통과분) 모집단이 달랐다. 둘 다 적는다."""
    from blog import daily_digest as dd
    rows = _rows()
    ds = {"complexes": rows, "count": len(rows), "data_asof": "2026-10-12", "generated": "2026-10-13"}
    band = next(b for b in dd._band_summary_rows(ds) if b["band"] == "10~15억")
    assert (band["n"], band["n_gated"]) == (7, 4)
    d = dd.build_daily_digest(ds, "2026-10-13", "2026-10-12")
    for body in (d["tistory_html"], d["site_html"]):
        seg = body[body.index("가격대별 요약"):]
        seg = seg[:seg.index("</table>")]
        assert "중위 집계" in seg and ">7(4)<" in seg
