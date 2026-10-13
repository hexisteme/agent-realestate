"""외부 리뷰(2026-10-13) C10·C11·C16 — 사례의 비교 표본·이상치 컷 민감도·검수 범위.

C10: 주요 상승·하락 사례가 두 비교 기간의 거래 수 없이 %만 보여 표본 구성 변화를 가늠할 수 없었다.
C11: 방법론의 '이상치 −40% 컷'이 결과를 얼마나 바꾸는지 확인할 수 없었다(Codex: 고정 단지 집합 변화와
     판정 대상 증감을 분리).
C16: 생성 시각과 사람 검수 여부가 구분되지 않았다 → '사람 검수 미실시' 명시, 집계 기준 변경 이력.
"""
from blog import build_explorer as be
from blog.daily_digest import build_daily_digest

ASOF = "2026-10-13"
LAWD = be.GU_LAWD["강남"]


def _trades(apt, area, prior, recent):
    return ([{"apt": apt, "area": area, "price": int(p * 1e8), "ym": "202601"} for p in prior]
            + [{"apt": apt, "area": area, "price": int(p * 1e8), "ym": "202609"} for p in recent])


def _published_row(name, area, recs):
    md, n = be._median_of(recs)
    t = be.derive_tier_now(recs, ASOF)
    return {"gu": "강남", "name": name, "area_m2": area, "product_type": "아파트", "molit_recent_eok": md,
            "molit_n": n, "molit_trend_dir": t["trend_dir"], "molit_trend_pct": t["trend_pct"],
            "molit_trend_n_recent": t["trend_n_recent"], "molit_trend_n_prior": t["trend_n_prior"]}


def _fixture():
    a = _trades("가람", 84.0, [10.0] * 10, [3.0, 3.0, 10.5, 10.5])     # 컷 켜면 ▲, 끄면 ▼ (고정 집합 방향 변화)
    b = _trades("나무", 59.0, [8.0] * 9 + [2.0], [8.0, 8.0])           # 컷 무관 보합(둘 다 비교)
    c = _trades("다솜", 84.0, [9.0] * 7 + [1.0], [9.5, 9.5])           # 컷 켜면 9건(비교 제외), 끄면 10건(비교)
    molit = {LAWD: a + b + c}
    rows = [_published_row("가람", 84.0, a), _published_row("나무", 59.0, b), _published_row("다솜", 84.0, c)]
    return {"complexes": rows, "data_asof": ASOF}, molit


def test_median_without_cut_keeps_low_records():
    recs = _trades("가람", 84.0, [10.0] * 3, [3.0])
    assert be._median_of(recs) == (10.0, 3)
    assert be._median_of(recs, cut=None) == (10.0, 4)                  # 3억도 표본에 남는다
    assert be.derive_tier_now(recs, ASOF, cut=None)["trend_n_recent"] == 1


def test_cut_sensitivity_separates_fixed_set_change_from_membership_change():
    ds, molit = _fixture()
    s = be.compute_outlier_cut_sensitivity(ds, molit)
    assert (s["n_rows"], s["n_unreproduced"]) == (3, 0)
    assert (s["n_records"], s["n_records_cut"], s["n_complexes_with_cut"]) == (36, 4, 3)
    assert s["with_cut"] == {"n_judged": 2, "up": 1, "down": 0}
    assert s["without_cut"] == {"n_judged": 3, "up": 1, "down": 1}
    assert s["fixed_set"] == {"n": 2, "n_dir_changed": 1}
    assert (s["judged_only_with_cut"], s["judged_only_without_cut"]) == (0, 1)


def test_cut_sensitivity_skips_rows_it_cannot_reproduce():
    ds, molit = _fixture()
    ds["complexes"][1]["molit_n"] = 99                                  # 발행값과 재계산이 다르면 민감도에서 뺀다
    s = be.compute_outlier_cut_sensitivity(ds, molit)
    assert (s["n_rows"], s["n_unreproduced"]) == (2, 1)


def test_cut_note_states_rule_counts_and_both_results():
    ds, molit = _fixture()
    html = be.render_outlier_cut_note(be.compute_outlier_cut_sensitivity(ds, molit))
    for frag in ("60% 미만", "36건 중 4건", "3곳", "2→3단지", "상승 1→1", "하락 0→1", "방향이 바뀌는 곳 1곳",
                 "컷을 꺼야만 비교되는 단지 1곳"):
        assert frag in html, frag
    assert be.render_outlier_cut_note(None) == ""


def _case_row(**kw):
    base = dict(gu="강남", name="가람", saeng="강남구", product_type="아파트", area_m2=84.0, molit_n=15,
                molit_recent_eok=20.0, molit_trend_dir="▲", molit_trend_pct=5.0, molit_pos_52w=100,
                molit_trend_n_recent=3, molit_trend_n_prior=12, jeonse_n=None, jeonse_ratio_complex_pct=None,
                gap_eok=None, turnover_pct=None)
    base.update(kw)
    return base


def test_c10_case_rows_show_both_window_counts():
    ds = {"complexes": [_case_row()], "count": 1, "data_asof": ASOF, "generated": ASOF}
    d = build_daily_digest(ds, ASOF, ASOF)
    for surface in ("tistory_html", "site_html"):
        assert "(최근 3·직전 12건)" in d[surface], surface
        assert "층·동·거래유형 구성 변화는 미확인" in d[surface], surface


def test_c16_footer_separates_generation_from_human_review():
    d = build_daily_digest({"complexes": []}, ASOF, ASOF)
    for surface in ("tistory_html", "site_html"):
        assert f"생성 {ASOF}" in d[surface] and "사람 검수 미실시" in d[surface], surface


def test_c16_methodology_lists_review_scope_and_dated_changes():
    from blog.build_site import render_method_changes
    html = render_method_changes()
    assert "사람 검수는 하지 않는다" in html
    assert "2026-10-14" in html and "소급 수정하지 않았다" in html


def test_c15_tistory_area_examples_are_most_sampled_per_price_band():
    """C15 — 59·84㎡ 비교가 가나다순 첫 단지였다. 84㎡ 중위 가격대마다 min(n59,n84) 최대 단지 1곳('표본 많은 예시')."""
    from test_daily_digest import _area_row
    rows = [_area_row(name="가나다첫", complex_no="1", med84_eok=9.0, n59=5, n84=5),
            _area_row(name="표본많음", complex_no="2", med84_eok=9.5, n59=9, n84=8),
            _area_row(name="한쪽만많음", complex_no="3", med84_eok=9.8, n59=30, n84=5),
            _area_row(name="고가예시", complex_no="4", med84_eok=22.0, n59=6, n84=6)]
    d = build_daily_digest({"complexes": rows, "count": 4, "data_asof": ASOF}, ASOF, ASOF)
    body = d["tistory_html"]
    assert "표본많음" in body and "고가예시" in body
    assert "가나다첫" not in body and "한쪽만많음" not in body
    assert body.index("표본많음") < body.index("고가예시")            # 가격대 오름차순
    assert "표본 많은 예시" in body and "84㎡ 중위 가격대" in body and "가나다순" not in body
    assert "가나다첫" in d["site_html"]                                 # 사이트는 전체 표
