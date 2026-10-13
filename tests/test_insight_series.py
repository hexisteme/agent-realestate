"""고유 통찰 연재 3종(blog.insight_series, 2026-10-13 SPEC-D1 + D2) 단위테스트 — 넓혀가기 가격차·보증금 변동 금액·예산 이하 체결 관측의
산식(원 단위)·신원 제외·결측≠0·창 분류 경계·서로 다른 계약일 반복·수집 완료 게이트·재현성·원고 금칙어."""
from __future__ import annotations
import json

import pytest

from blog import insight_series as ins
from blog.tistory_contract import parse_helper

LAWD = "11530"   # 구로
MONTHS = ins.months_between("202510", "202609")


def _done():
    return [f"{lawd}|{ym}" for lawd in ins.be.GU_LAWD.values() for ym in MONTHS]


def _trade(apt, area, eok, ym, day=10):
    return {"apt": apt, "area": area, "price": int(round(eok * 1e8)), "ym": ym,
            "deal_date": f"{ym[:4]}-{ym[4:]}-{day:02d}", "cancellation_checked": True}


def _row(name, cno, flag="", area=84.0, **kw):
    r = dict(name=name, gu="구로", complex_no=cno, product_type="아파트", area_m2=area, spread_flag=flag,
             molit_n=20, molit_recent_eok=9.0, jeonse_n=10, jeonse_recent_eok=4.5, jeonse_ratio_complex_pct=50.0)
    r.update(kw)
    return r


def _molit(trades):
    return {LAWD: trades, "_done": _done()}


# ── 공통 게이트 ────────────────────────────────────────────────────────────

def test_require_complete_months_fails_on_missing_month():
    m = _molit([])
    m["_done"] = [d for d in m["_done"] if d != f"{LAWD}|202603"]
    with pytest.raises(ValueError, match="202603"):
        ins.require_complete_months(m, ["202603"])
    ins.require_complete_months(_molit([]), ["202603"])


def test_panel_excludes_blocked_identity_and_non_apartment_and_rejects_duplicates():
    ds = {"complexes": [_row("가", "1"), _row("나", "2", flag="ambiguous_name"), _row("다", "3", flag="kapt_area_mismatch"),
                        _row("라", "4", product_type="주상복합"), _row("마", "5", flag="identity_unverified")]}
    assert [r["name"] for r in ins.select_panel_rows(ds)] == ["가", "마"]
    with pytest.raises(ValueError, match="중복"):
        ins.select_panel_rows({"complexes": [_row("가", "1"), _row("가2", "1")]})


# ── 연재 1: 같은 단지 넓혀가기 가격차 ──────────────────────────────────────

def test_area_upsize_uses_raw_won_and_keeps_inversion_but_needs_verified_identity():
    trades = ([_trade("넓자이", 59.9, 8.0 + i * 0.01, "202603", i + 1) for i in range(5)]
              + [_trade("넓자이", 84.9, 9.0 + i * 0.01, "202604", i + 1) for i in range(5)]
              + [_trade("역전푸르", 59.0, 10.0, "202603", i + 1) for i in range(5)]
              + [_trade("역전푸르", 84.0, 9.8, "202604", i + 1) for i in range(5)]
              + [_trade("미확인", 59.0, 5.0, "202603", i + 1) for i in range(5)]
              + [_trade("미확인", 84.0, 6.0, "202604", i + 1) for i in range(5)]
              + [_trade("적은표본", 59.0, 5.0, "202603", i + 1) for i in range(4)]
              + [_trade("적은표본", 84.0, 6.0, "202604", i + 1) for i in range(9)])
    ds = {"complexes": [_row("넓자이", "1"), _row("역전푸르", "2", flag="total_inversion"),
                        _row("미확인", "3", flag="identity_unverified"), _row("적은표본", "4")]}
    res = ins.compute_area_upsize(ds, _molit(trades))
    by = {c["name"]: c for c in res["complexes"]}
    assert set(by) == {"넓자이", "역전푸르"}
    assert by["넓자이"]["diff_won"] == 100_000_000 and by["넓자이"]["med59_won"] == 802_000_000
    assert by["넓자이"]["premium_pct"] == pytest.approx((902 / 802 - 1) * 100)
    assert by["역전푸르"]["diff_won"] == -20_000_000 and by["역전푸르"]["premium_pct"] < 0
    assert res["n_nonpositive"] == 1
    assert by["넓자이"]["band"] == "8~10억" and by["역전푸르"]["band"] == "10~15억"
    bands = {b["band"]: b for b in res["bands"]}
    assert bands["8~10억"]["n"] == 1 and bands["6억 미만"]["n"] == 0 and bands["6억 미만"]["diff_median_eok"] is None


# ── 연재 2: 보증금 변동 금액 ──────────────────────────────────────────────

def test_deposit_change_amounts_and_ratio_identity():
    ds = {"complexes": [_row("가", "1", molit_recent_eok=8.0, jeonse_recent_eok=6.0, jeonse_ratio_complex_pct=75.0),
                        _row("나", "2", molit_recent_eok=10.0, jeonse_recent_eok=5.0, jeonse_ratio_complex_pct=50.0),
                        _row("다", "3", flag="ambiguous_name"),
                        _row("라", "4", jeonse_n=4),
                        _row("마", "5", jeonse_ratio_complex_pct=96.0)]}
    res = ins.compute_deposit_change(ds)
    by = {c["name"]: c for c in res["complexes"]}
    assert set(by) == {"가", "나"}
    assert by["가"]["amount_man"][0.10] == 6000 and by["가"]["amount_man"][0.05] == 3000
    assert by["가"]["ratio_pct"][0.10] == pytest.approx(30.0)          # 0.1·0.75/0.25
    assert by["나"]["ratio_pct"][0.10] == pytest.approx(10.0)          # 0.1·0.5/0.5
    assert by["나"]["band"] == "50~60%" and by["가"]["band"] == "70% 이상"
    empty = {b["band"]: b for b in res["bands"]}["40% 미만"]
    assert empty["n"] == 0 and empty["amount10_median_man"] is None


def test_deposit_band_uses_unrounded_ratio():
    """G3(Codex): 발행 전세가율은 소수 1자리 반올림 — 구간은 원 단위 J/S 로 정한다(성북 극동 59.97% 실측)."""
    ds = {"complexes": [_row("극", "1", molit_recent_eok=6.67, jeonse_recent_eok=4.0, jeonse_ratio_complex_pct=60.0)]}
    c = ins.compute_deposit_change(ds)["complexes"][0]
    assert c["band"] == "50~60%"
    assert c["jeonse_ratio_pct"] == pytest.approx(4.0 / 6.67 * 100)


# ── 연재 3: 예산 이하 체결 관측 ───────────────────────────────────────────

def _window_trades(apt, eoks, ym, days=None):
    days = days or list(range(1, len(eoks) + 1))
    return [_trade(apt, 84.0, e, ym, d) for e, d in zip(eoks, days)]


def test_classify_window_short_vs_zero_and_distinct_days():
    B = 1_000_000_000
    assert ins.classify_window([], B) == "short"
    four = _window_trades("x", [9.0] * 4, "202602")
    assert ins.classify_window(four, B) == "short"                    # n<5 는 0 이 아니라 표본 부족
    same_day = _window_trades("x", [11.0] * 5, "202602", days=[3] * 5)
    assert ins.classify_window(same_day, B) == "short"                # 계약일 1개
    assert ins.classify_window(_window_trades("x", [11.0] * 5, "202602"), B) == "0"
    one = _window_trades("x", [9.5, 9.5, 11, 11, 11], "202602", days=[4, 4, 5, 6, 7])
    assert ins.classify_window(one, B) == "1"                         # 같은 날 2건 = 1 계약일
    assert ins.classify_window(_window_trades("x", [10.0, 10.0, 11, 11, 11], "202602"), B) == "2+"   # 경계 포함
    assert ins.classify_window(_window_trades("x", [9, 9, 9, 11, 11], "202602"), B, repeat_days=3) == "2+"
    assert ins.classify_window(_window_trades("x", [9, 9, 11, 11, 11], "202602"), B, repeat_days=3) == "1"


def test_budget_watch_cross_table_area_bounds_and_september_excluded():
    tr = []
    tr += _window_trades("유지", [9, 9, 9, 11, 11], "202602") + _window_trades("유지", [9, 9.5, 11, 11, 11], "202606")
    tr += _window_trades("올라감", [9, 9, 11, 11, 11], "202603") + _window_trades("올라감", [11] * 5, "202607")
    tr += _window_trades("내려옴", [11] * 5, "202601") + _window_trades("내려옴", [9, 9, 11, 11, 11], "202605")
    tr += _window_trades("부족", [9] * 5, "202602") + _window_trades("부족", [9] * 5, "202609")   # 9월은 창 밖
    tr += [_trade("경계", 80.5, 9, "202602", d) for d in range(1, 6)] + [_trade("경계", 87.5, 9, "202606", d) for d in range(1, 6)]
    tr += [_trade("경계밖", 87.6, 9, "202602", d) for d in range(1, 6)] + [_trade("경계밖", 80.4, 9, "202606", d) for d in range(1, 6)]
    ds = {"complexes": [_row("유지", "1"), _row("올라감", "2", flag="identity_unverified"), _row("내려옴", "3"),
                        _row("부족", "4"), _row("경계", "5", area=59.0), _row("경계밖", "6"), _row("모호", "7", flag="ambiguous_name")]}
    res = ins.compute_budget_watch(ds, _molit(tr))
    assert res["n_panel"] == 6 and res["n_pass_both"] == 4
    assert res["cross"]["2+|2+"] == 2 and res["cross"]["2+|0"] == 1 and res["cross"]["0|2+"] == 1
    assert sum(res["cross"].values()) == res["n_pass_both"]
    assert res["short"]["B"] == 1 and res["short"]["no_trades"] == 1
    lost = res["cells"]["2+|0"][0]
    assert lost["name"] == "올라감" and lost["identity"] == "미확인" and lost["n"] == {"A": 5, "B": 5}
    assert {c["name"] for c in res["cells"]["2+|2+"]} == {"유지", "경계"}
    verified = ins.compute_budget_watch(ds, _molit(tr), verified_only=True)
    assert verified["cross"]["2+|0"] == 0 and verified["n_panel"] == 5


def test_budget_watch_fixed_cut_uses_prior_window_only():
    tr = _window_trades("컷", [9, 9, 9, 9, 9], "202602") + _window_trades("컷", [5, 5, 16, 16, 16, 16, 16], "202606")
    ds = {"complexes": [_row("컷", "1")]}
    plain = ins.compute_budget_watch(ds, _molit(tr))
    cut = ins.compute_budget_watch(ds, _molit(tr), prior_cut=0.6)
    assert plain["cross"]["2+|2+"] == 1
    assert cut["cross"]["2+|0"] == 1                                   # 5억 2건은 이전 창 중위 9억×0.6=5.4억 미만 → 제외


def test_budget_watch_grid_is_complete_and_ordered():
    tr = _window_trades("유지", [9, 9, 9, 11, 11], "202602") + _window_trades("유지", [9, 9.5, 11, 11, 11], "202606")
    grid = ins.compute_budget_watch_grid({"complexes": [_row("유지", "1")]}, _molit(tr))
    keys = [g["setting"] for g in grid]
    assert len(keys) == len(set(keys)) == 3 * 2 * 2 + 5   # SPEC-D2: 군집 분리 보류 없는 D1 비교 행 1개 추가
    assert keys[0] == "주설정" and keys[-1] == "가격 군집 분리 보류 없음(SPEC-D1)"


# ── 재현성·원고 ───────────────────────────────────────────────────────────

def _fixture():
    tr = []
    for i, (n, e59, e84) in enumerate([("가자이", 7.0, 8.2), ("나푸르", 12.0, 13.5)]):
        tr += [_trade(n, 59.0, e59, "202603", d) for d in range(1, 6)] + [_trade(n, 84.0, e84, "202604", d) for d in range(1, 6)]
        tr += _window_trades(n, [e84 - 0.5] * 5, "202602") + _window_trades(n, [e84] * 5, "202606")
    ds = {"complexes": [_row("가자이", "1", molit_recent_eok=8.2, jeonse_recent_eok=5.0, jeonse_ratio_complex_pct=61.0),
                        _row("나푸르", "2", molit_recent_eok=13.5, jeonse_recent_eok=6.0, jeonse_ratio_complex_pct=44.4)],
          "data_asof": "2026-10-13"}
    return ds, _molit(tr)


def test_build_is_reproducible_and_json_serializable():
    ds, m = _fixture()
    a, b = ins.build_series_results(ds, m), ins.build_series_results(ds, m)
    assert a == b
    json.dumps(a, ensure_ascii=False)


def test_drafts_pass_wording_guard_and_contract(tmp_path):
    ds, m = _fixture()
    res = ins.build_series_results(ds, m)
    paths = ins.write_series_drafts(res, "2026-10-13", str(tmp_path))
    assert len(paths) == 3
    for p in paths:
        payload = parse_helper(p)
        body = payload["body"]
        for banned in ("갭", "자기자본", "호가", "추천", "매수 가능", "사다리"):
            assert banned not in body and banned not in payload["title"]
        for label in ("[관측]", "[해석]", "판단이 달라질 조건", "현장에서 확인할 것", "2026-11-10"):
            assert label in body
    watch = parse_helper(paths[2])["body"]
    assert "살 수 있다는 뜻이 아닙니다" in watch
    deposit = parse_helper(paths[1])["body"]
    assert "[가정]" in deposit and "갱신" in deposit


# ── SPEC-D2: 같은 면적대 안 정확 면적별 가격 군집 분리(동명 단지 병합 의심) 보류 ──────────

def test_detect_area_price_split_threshold_and_min_group():
    hi = [_trade("x", 84.92, 17.0, "202602", d) for d in range(1, 4)]
    lo = [_trade("x", 84.89, 9.0, "202602", d) for d in range(4, 7)]
    assert ins.detect_area_price_split(hi + lo)                         # 17/9 = 1.89 ≥ 1.4
    assert not ins.detect_area_price_split(hi + lo[:2])                 # 낮은 군집 2건 < 3건
    near = [_trade("x", 84.89, 13.0, "202602", d) for d in range(4, 7)]
    assert not ins.detect_area_price_split(hi + near)                   # 17/13 = 1.31 < 1.4


def test_split_complex_is_held_in_all_three_series():
    split = ([_trade("섞임", 84.92, 17.0, "202602", d) for d in range(1, 6)] + [_trade("섞임", 84.89, 9.0, "202603", d) for d in range(1, 6)]
             + [_trade("섞임", 84.92, 17.5, "202606", d) for d in range(1, 6)] + [_trade("섞임", 84.89, 9.5, "202607", d) for d in range(1, 6)]
             + [_trade("섞임", 59.0, 12.0, "202604", d) for d in range(1, 6)])
    ds = {"complexes": [_row("섞임", "1", area=84.0, molit_recent_eok=15.0, jeonse_recent_eok=7.0, jeonse_ratio_complex_pct=46.7)]}
    m = _molit(split)
    up = ins.compute_area_upsize(ds, m)
    assert up["n"] == 0 and [c["name"] for c in up["split_hold"]] == ["섞임"]
    dep = ins.compute_deposit_change(ds, molit=m)
    assert dep["n"] == 0 and dep["n_split_hold"] == 1
    assert ins.compute_deposit_change(ds)["n"] == 1                     # 원거래 없이 부르면 가드 없음(발행값만)
    w = ins.compute_budget_watch(ds, m)
    assert w["n_pass_both"] == 0 and w["n_split_hold"] == 1
    assert ins.compute_budget_watch(ds, m, split_guard=False)["n_pass_both"] == 1


def test_watch_pooled_share():
    tr = _window_trades("가", [9, 9, 11, 11, 11], "202602") + _window_trades("가", [9, 11, 11, 11, 11, 11, 11, 11], "202606")
    w = ins.compute_budget_watch({"complexes": [_row("가", "1")]}, _molit(tr))
    assert w["share_le_pooled_pct"] == {"A": 40.0, "B": 12.5}
    assert w["n_trades"] == {"A": 5, "B": 8}   # 통과 단지 거래 수 — 두 기간 건수 차이를 원고에 공개


def test_display_rounding_is_decimal_half_up():
    """G2(Codex): 표시 반올림은 십진 HALF_UP — 이진 float·은행가 반올림이면 4.175억→4.17, 2162.5만→2162 로 틀린다."""
    assert ins._eok_txt(4.175) == "4.18억원" and ins._eok_txt(2.425) == "2.43억원" and ins._eok_txt(1.015) == "1.02억원"
    assert ins._man_txt(2162.5) == "2,163만원"
    assert ins._pct_txt(0.25) == "+0.3%" and ins._pct_txt(0.25, False) == "0.3%"
    ds = {"complexes": [_row("반", "1", molit_recent_eok=9.0, jeonse_recent_eok=4.325, jeonse_ratio_complex_pct=48.1)]}
    c = ins.compute_deposit_change(ds)["complexes"][0]
    assert c["amount_man"][0.05] == 2162.5 and c["amount_man"][0.10] == 4325   # 단지 값은 반올림하지 않음(G3) — 표시에서만
    assert ins.compute_deposit_change(ds)["amount_median_man"][0.05] == 2163


# ── G3(Codex) 반례·회귀 ──────────────────────────────────────────────────

def test_deposit_band_boundary_is_exact():
    ds = {"complexes": [_row("육", "1", molit_recent_eok=6.7, jeonse_recent_eok=4.02),     # 정확히 60%
                        _row("칠", "2", molit_recent_eok=5.8, jeonse_recent_eok=4.06)]}    # 정확히 70%
    by = {c["name"]: c["band"] for c in ins.compute_deposit_change(ds)["complexes"]}
    assert by == {"육": "60~70%", "칠": "70% 이상"}


def test_deposit_diff_median_is_taken_in_won():
    ds = {"complexes": [_row("가", "1", molit_recent_eok=12.0, jeonse_recent_eok=4.0),
                        _row("나", "2", molit_recent_eok=12.01, jeonse_recent_eok=4.0)]}
    res = ins.compute_deposit_change(ds)
    assert res["diff_median_eok"] == 800_500_000 / 1e8 and ins._eok_txt(res["diff_median_eok"]) == "8.01억원"


def test_panel_rejects_missing_complex_no_and_allows_same_id_in_other_gu():
    with pytest.raises(ValueError, match="complex_no"):
        ins.select_panel_rows({"complexes": [_row("가", None), _row("나", None)]})
    rows = ins.select_panel_rows({"complexes": [_row("가", "7"), _row("나", "7", gu="강서")]})
    assert len(rows) == 2


def test_completeness_is_checked_inside_each_series():
    m = _molit([])
    m["_done"] = [d for d in m["_done"] if d != f"{LAWD}|202609"]
    ds = {"complexes": [_row("가", "1")]}
    with pytest.raises(ValueError, match="202609"):
        ins.compute_deposit_change(ds, molit=m)
    with pytest.raises(ValueError, match="202609"):
        ins.compute_budget_watch(ds, m)                     # 군집 판정이 9월까지 쓰므로
    assert ins.compute_budget_watch(ds, m, split_guard=False)["n_panel"] == 1


def test_watch_pooled_share_differs_from_median_of_shares():
    tr = (_window_trades("전부", [9] * 5, "202602") + _window_trades("전부", [9] * 5, "202606")
          + _window_trades("없음", [12] * 15, "202602") + _window_trades("없음", [12] * 15, "202606"))
    w = ins.compute_budget_watch({"complexes": [_row("전부", "1"), _row("없음", "2")]}, _molit(tr))
    assert w["share_le_pooled_pct"]["A"] == 25.0 and w["share_le_median_pct"]["A"] == 50.0


def test_watch_excludes_shared_source_name():
    tr = _window_trades("같은", [9] * 5, "202602") + _window_trades("같은", [9] * 5, "202606")
    w = ins.compute_budget_watch({"complexes": [_row("같은", "1"), _row("같은", "2")]}, _molit(tr))
    assert w["n_shared_source"] == 2 and w["n_pass_both"] == 0


def test_split_threshold_is_inclusive_at_exactly_1_4():
    hi = [_trade("x", 84.92, 14.0, "202603", d) for d in (1, 2, 3)]
    assert ins.detect_area_price_split(hi + [_trade("x", 84.89, 10.0, "202603", d) for d in (1, 2, 3)])
    assert not ins.detect_area_price_split(hi + [_trade("x", 84.89, 10.01, "202603", d) for d in (1, 2, 3)])


def test_verified_only_keeps_total_inversion():
    tr = _window_trades("역전", [9] * 5, "202602") + _window_trades("역전", [9] * 5, "202606")
    w = ins.compute_budget_watch({"complexes": [_row("역전", "1", flag="total_inversion")]}, _molit(tr), verified_only=True)
    assert w["n_pass_both"] == 1


def test_drafts_render_with_empty_samples(tmp_path):
    ds = {"complexes": [], "data_asof": "2026-10-13"}
    res = ins.build_series_results(ds, _molit([]))
    for p in ins.write_series_drafts(res, "2026-10-13", str(tmp_path)):
        assert "—" in parse_helper(p)["body"]


def test_budget_panel_file_lists_full_panel():
    ds, m = _fixture()
    res = ins.build_series_results(ds, m)
    panel = res["watch"]["panel"]
    assert {(p["gu"], p["complex_no"]) for p in panel} == {(r["gu"], r["complex_no"]) for r in ins.select_panel_rows(ds)}
    assert all(p["status"] for p in panel)


# ── R2(Codex) 잔여 ──────────────────────────────────────────────────────

def test_upsize_diff_median_is_taken_in_won():
    tr = []
    for n, e84 in (("가", 18.0), ("나", 18.01)):
        tr += [_trade(n, 59.0, 10.0, "202603", d) for d in range(1, 6)] + [_trade(n, 84.0, e84, "202604", d) for d in range(1, 6)]
    res = ins.compute_area_upsize({"complexes": [_row("가", "1"), _row("나", "2")]}, _molit(tr))
    assert res["diff_median_eok"] == 800_500_000 / 1e8 and ins._eok_txt(res["diff_median_eok"]) == "8.01억원"


def test_clock_without_trades_is_not_zero(tmp_path):
    res = ins.build_series_results({"complexes": [], "data_asof": "2026-10-13"}, _molit([]))
    for p in ins.write_series_drafts(res, "2026-10-13", str(tmp_path)):
        body = parse_helper(p)["body"]
        assert "계약 0건" not in body and "중위 0건" not in body
