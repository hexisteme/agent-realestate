"""R2 적대 검증(Codex·agy, 2026-10-13) 채택 결함 — 07-codex-r2.md · 08-agy-r2.md.

C11-분모·재현 / X1-인덱스 / W1-신원 / C13-창경계 / F5 수집 누락 구 / C14 변경·비교일·결측 / C3 제외 사유 /
C15 실데이터 축약 / F7 구 요약 모집단 / F8 카드 alt 분모 / F9 보합 / F12 적용 시점.
"""
from datetime import date

import pytest

from blog import build_explorer as be
from blog import daily_digest as dd
from blog import daily_freshness as dfr
from blog import market_charts as mch
from blog import period_delta as pd
from test_review_fix_method import ASOF, LAWD, _fixture, _published_row, _trades


# ── C11 ──────────────────────────────────────────────────────────────────────

def test_c11_denominator_counts_single_record_complexes():
    ds, molit = _fixture()
    one = _trades("라온", 84.0, [9.0], [])
    molit[LAWD] += one
    ds["complexes"].append(_published_row("라온", 84.0, one))
    s = be.compute_outlier_cut_sensitivity(ds, molit)
    assert (s["n_rows"], s["n_records"], s["n_records_cut"]) == (4, 37, 4)


def test_c11_reproduction_also_checks_trend_fields():
    ds, molit = _fixture()
    ds["complexes"][0]["molit_trend_dir"] = "▼"                    # 중위·n 은 같고 추세만 다른 발행값
    s = be.compute_outlier_cut_sensitivity(ds, molit)
    assert (s["n_rows"], s["n_unreproduced"]) == (2, 1)


# ── X1-인덱스 ────────────────────────────────────────────────────────────────

def test_index_band_chips_separate_observed_and_median_population():
    from blog.build_site import render_band_chips
    html = render_band_chips([{"band": "10억 미만", "n": 825, "n_gated": 305, "median": 7.17}])
    assert "관측 825단지" in html and "중위 7.17억(집계 305단지)" in html


# ── W1 ───────────────────────────────────────────────────────────────────────

def _row(name, area, no, price=10.0, gu="송파"):
    return dict(gu=gu, name=name, area_m2=area, molit_recent_eok=price, molit_n=12, product_type="아파트",
                molit_pos_52w=50, complex_no=no)


def _delta(base_rows, cur_rows):
    base = {"complexes": base_rows, "count": len(base_rows), "data_asof": "2026-10-04"}
    cur = {"complexes": cur_rows, "count": len(cur_rows), "data_asof": "2026-10-11"}
    return pd.build_period_delta(cur, base, "weekly", "2026-10-11", date(2026, 10, 4))


def test_w1_renamed_complex_with_same_area_is_a_price_pair():
    d = _delta([_row("래미안(구)", 84.0, "A100")], [_row("래미안", 84.0, "A100")])
    assert d["n_pairs"] == 1 and d["area_changed"] == [] and d["added"] == [] and d["removed"] == []


def test_w1_same_name_and_area_with_different_complex_no_is_not_paired():
    d = _delta([_row("현대", 84.0, "B1", 10.0)], [_row("현대", 84.0, "B2", 20.0)])
    assert d["n_pairs"] == 0 and d["added"] == ["현대(송파)"] and d["removed"] == ["현대(송파)"]


def test_w1_complex_no_missing_on_one_side_falls_back_to_name():
    d = _delta([_row("거여1단지", 50.0, "")], [_row("거여1단지", 39.5, "C7")])
    assert d["area_changed"] == ["거여1단지(송파) 50→39.5㎡"]
    assert d["added"] == [] and d["removed"] == []


# ── C13·F5 ───────────────────────────────────────────────────────────────────

def _rec(apt, ym):
    return {"apt": apt, "area": 59.9, "price": 100000, "ym": ym}


def test_c13_window_exit_uses_collection_window_not_observed_minimum():
    prev = {"11710": [_rec("가", "202601"), _rec("나", "202609")]}
    cur = {"11710": [_rec("나", "202609")]}
    f = pd.diff_filings(prev, cur, window_start="202510")
    assert (f["n_gone_window"], f["n_gone_other"]) == (0, 1)
    assert pd.diff_filings(prev, cur)["n_gone_window"] is None       # 창 경계를 모르면 사유 미확인


def test_collection_window_start_matches_month_windows():
    assert pd.molit_window_start("2026-10-13") == "202510"


def test_f5_district_missing_today_is_disclosed_not_silently_dropped():
    prev = {"11710": [_rec("가", "202609")], "11680": [_rec("나", "202609")]}
    cur = {"11710": [_rec("가", "202609")]}
    f = pd.diff_filings(prev, cur, window_start="202510")
    assert f["n_gone"] == 0 and f["n_lawd_skipped"] == 1
    assert "수집 누락 1개 구" in pd.filing_gone_txt(f)


# ── C14 ──────────────────────────────────────────────────────────────────────

def _macro(asof, errors=(), **vals):
    return {"asof": asof, "errors": {c: "fail" for c in errors},
            "indicators": {c: {"code": c, "label": lab, "value": v} for c, (lab, v) in vals.items()}}


def test_c14_indicator_absent_yesterday_is_no_base_not_changed():
    prev = _macro("2026-10-12", errors=("cd91",), kr3y=("국고채 3년", 3.9))
    cur = _macro("2026-10-13", cd91=("CD 91일", 3.24), kr3y=("국고채 3년", 3.9))
    m = dfr.compare_macro(prev, cur)
    assert m["changed"] == [] and m["no_base"] == ["CD 91일"] and m["n_unchanged"] == 1


def test_c14_each_line_states_its_own_base_date():
    prev = _macro("2026-10-12", kr3y=("국고채 3년", 3.9))
    cur = _macro("2026-10-13", kr3y=("국고채 3년", 3.8))
    fr = dfr.build_daily_freshness("2026-10-13", {"11710": [_rec("가", "202609")]},
                                   ("2026-10-10", {"11710": []}), cur, prev)
    html = dfr.render_freshness_html(fr)
    assert "국토부 실거래 목록(2026-10-10 자료 대비)" in html and "거시 지표(2026-10-12 스냅샷 대비)" in html


def test_c14_failures_are_shown_even_without_any_baseline():
    cur = _macro("2026-10-13", errors=("cd91",), kr3y=("국고채 3년", 3.8))   # 실패 지표는 indicators 에서 빠진다
    html = dfr.render_freshness_html(dfr.build_daily_freshness("2026-10-13", {"11710": []}, None, cur, None))
    assert "직전 아카이브 없음" in html and "직전 스냅샷 없음" in html and "오늘 수집 실패 1개" in html
    assert dfr.build_daily_freshness("2026-10-13", None, None, None, None) is None


# ── C3·F9 ────────────────────────────────────────────────────────────────────

def test_c3_summary_lists_exclusion_counts_and_f9_flat():
    rows = ([dict(product_type="아파트", area_m2=59.0, molit_n=12, molit_recent_eok=9.0,
                  molit_trend_pct=p, molit_trend_dir=d, molit_trend_n_recent=3) for p, d in ((3.0, "▲"), (0.0, "—"))]
            + [dict(product_type="주상복합", area_m2=59.0, molit_n=12, molit_recent_eok=9.0)]
            + [dict(product_type="아파트", area_m2=59.0, molit_n=0, molit_recent_eok=None)])
    counts = {"n_total": len(rows), **be.count_gated_trends(rows), "funnel": be.compute_observation_funnel(rows)}
    text = " ".join(mch.observation_summary_lines(counts, None, asof=ASOF))
    assert "보합 1" in text
    assert "가격 미산출 1" in text and "비아파트 1" in text and "한 기간이라도 2건 미만 0" in text
    title = dd._build_digest_title(ASOF, {"n_judged": 2, "up": 1, "down": 0, "n_total": 4}, {})
    assert title == f"서울 아파트 실거래가 — 비교 2단지, 최근 3개월 vs 직전 9개월 ({ASOF})"   # 방향별 수는 본문(관측 요약)


# ── C15 ──────────────────────────────────────────────────────────────────────

def test_c15_truncated_examples_say_how_many_bands_are_shown():
    from test_daily_digest import _area_row
    rows = [_area_row(name="싼", complex_no="1", med84_eok=9.0), _area_row(name="비싼", complex_no="2", med84_eok=22.0)]
    html = dd._render_area_tracks_tistory(rows, ASOF, ASOF, limit=1)
    assert "2개 가격대 중 1개 표시" in html


def test_c15_visual_first_trim_keeps_area_examples_before_inventory():
    stages = dd.trim_stages(visual_first=True)
    first_area_cut = next(i for i, (_, _, area) in enumerate(stages) if area < 4)
    assert all(inv == 0 for _, inv, _ in stages[:first_area_cut][-1:])
    assert all(detail == 0 for detail, _, _ in stages)


# ── F7·F8·F12 ────────────────────────────────────────────────────────────────

def test_f7_gu_summary_trend_cell_names_its_denominator():
    from test_daily_digest import _sample_ds
    site = dd.build_daily_digest(_sample_ds(), "2026-09-05", "2026-09-04")["site_html"]
    assert "비교 1 중 <span class=up>▲" in site


def test_f8_cover_alt_limits_share_to_comparable_complexes(tmp_path):
    pytest.importorskip("PIL")
    from blog import thumbnail_card as card
    from test_thumbnail_card import _dataset
    try:
        font = card._resolve_font()
    except FileNotFoundError:
        pytest.skip("Korean font unavailable on this host")
    alt = card.build_daily_card(_dataset(), tmp_path, font_path=font, cover_safe=True).alt
    assert "추세 비교가 가능한 단지 중" in alt


def test_f12_method_change_date_names_the_run():
    from blog.build_site import render_method_changes
    assert "07:05 자동 실행분부터" in render_method_changes()
