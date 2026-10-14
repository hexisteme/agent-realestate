"""연재 정기 발행(blog.series_edition, 2026-10-14 설계 report/2026-10-14-series-automation/DESIGN.md) 단위테스트 —
일정(N번째 화요일·시작일), 이동 기간, SPEC-D3(단지일련번호·신규 계약·직거래/저층), 지난 판 대비 판정, 판 기록·보류 흐름."""
from __future__ import annotations
import datetime as dt
import json

import pytest

from blog import insight_series as ins
from blog import series_edition as se

LAWD = "11530"   # 구로
D = dt.date


def _done(months):
    return [f"{lawd}|{ym}" for lawd in ins.be.GU_LAWD.values() for ym in months]


def _trade(apt, area, eok, ym, day=10, **kw):
    x = {"apt": apt, "area": area, "price": int(round(eok * 1e8)), "ym": ym, "deal_date": f"{ym[:4]}-{ym[4:]}-{day:02d}",
         "cancellation_checked": True, "apt_seq": f"11530-{apt}", "floor": 7, "dealing": "중개거래"}
    x.update(kw)
    return x


def _row(name, cno, flag="", area=84.0, **kw):
    r = dict(name=name, gu="구로", complex_no=cno, product_type="아파트", area_m2=area, spread_flag=flag,
             molit_n=20, molit_recent_eok=9.0, jeonse_n=10, jeonse_recent_eok=4.5, jeonse_ratio_complex_pct=50.0)
    r.update(kw)
    return r


# ── 일정 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("day,kind", [
    (D(2026, 11, 10), "upsize"), (D(2026, 11, 17), "deposit"), (D(2026, 11, 24), "watch"),
    (D(2026, 11, 3), None),                              # 첫째 화요일
    (D(2026, 12, 29), None),                             # 다섯째 화요일
    (D(2026, 10, 20), None), (D(2026, 10, 27), None),    # 시작일 전 — 10-14 발행본 직후 중복 금지
    (D(2026, 11, 11), None),                             # 수요일
    (D(2027, 1, 12), "upsize"), (D(2027, 1, 19), "deposit"), (D(2027, 1, 26), "watch")])
def test_due_kind_follows_nth_tuesday_and_start_date(day, kind):
    assert se.due_kind(day) == kind


def test_next_due_is_same_nth_tuesday_next_month():
    assert se.next_due("upsize", D(2026, 11, 10)) == D(2026, 12, 8)
    assert se.next_due("deposit", D(2026, 11, 17)) == D(2026, 12, 15)
    assert se.next_due("watch", D(2026, 12, 22)) == D(2027, 1, 26)


def test_expected_last_month_is_previous_calendar_month():
    assert se.expected_last_month(D(2026, 11, 10)) == "202610"
    assert se.expected_last_month(D(2027, 1, 12)) == "202612"


# ── 이동 기간 ─────────────────────────────────────────────────────────────

def test_default_windows_equal_1013_constants():
    w = ins.DEFAULT_WINDOWS
    assert w.year == ins.YEAR_WINDOW and w.upsize_recent == ins.UPSIZE_RECENT and w.upsize_no_last == ins.UPSIZE_NO_SEPT
    assert w.watch == ins.WATCH_WINDOWS and w.watch_alt == ins.WATCH_ALT_WINDOWS and w.watch_last_incl == ins.WATCH_SEPT_WINDOWS


def test_windows_cross_year_boundary():
    assert ins.shift_month("202601", -1) == "202512" and ins.shift_month("202612", 1) == "202701"
    w = ins.windows_for("202602")
    assert w.year == ("202503", "202602")
    assert w.watch == (("202506", "202509"), ("202510", "202601"))
    assert w.watch_last_incl == (("202507", "202510"), ("202511", "202602"))


# ── SPEC-D3 ───────────────────────────────────────────────────────────────

M10 = ins.windows_for("202610")
MONTHS10 = ins.months_between(*M10.year)


def _molit10(trades):
    return {LAWD: trades, "_done": _done(MONTHS10)}


def _pair(apt, lo=8.0, hi=9.0, ym="202606", **kw):
    return ([_trade(apt, 59.0, lo + i * 0.01, ym, i + 1, **kw) for i in range(5)]
            + [_trade(apt, 84.0, hi + i * 0.01, ym, i + 1, **kw) for i in range(5)])


def test_d3_upsize_holds_complex_with_two_or_missing_apt_seq():
    trades = _pair("하나") + _pair("둘") + [_trade("둘", 84.0, 9.0, "202607", 20, apt_seq="11530-다른")]
    trades += _pair("셋") + [_trade("셋", 84.0, 9.0, "202605", i % 28 + 1) for i in range(100)]
    trades += [_trade("셋", 84.0, 9.0, "202607", 21, apt_seq=None)]          # 결측 1행(132행 중) — 전체 비율은 통과해도 그 단지는 확인 불가
    ds = {"complexes": [_row("하나", "1"), _row("둘", "2"), _row("셋", "3")]}
    res = ins.compute_area_upsize(ds, _molit10(trades), win=M10, spec=ins.SPEC_D3)
    assert [c["name"] for c in res["complexes"]] == ["하나"]
    assert [h["name"] for h in res["seq_hold"]] == ["둘", "셋"]
    d2 = ins.compute_area_upsize(ds, _molit10(trades), win=M10)
    assert "seq_hold" not in d2 and len(d2["complexes"]) == 3


def test_d3_coverage_gate_raises_spec_not_applicable():
    trades = _pair("하나", apt_seq=None)
    ds = {"complexes": [_row("하나", "1")]}
    with pytest.raises(ins.SpecNotApplicable, match="apt_seq"):
        ins.compute_area_upsize(ds, _molit10(trades), win=M10, spec=ins.SPEC_D3)


def test_d3_upsize_sensitivity_rows_drop_direct_and_low_floor_trades():
    trades = _pair("하나") + [_trade("하나", 84.0, 20.0, "202607", 25 + i, dealing="직거래") for i in range(3)]
    trades += [_trade("하나", 59.0, 1.0, "202607", 25 + i, floor=1) for i in range(3)]
    trades += [_trade("하나", 59.0, 8.0, "202608", 1, floor=None)]
    ds = {"complexes": [_row("하나", "1")]}
    res = ins.compute_area_upsize(ds, _molit10(trades), win=M10, spec=ins.SPEC_D3)
    sens = {e["filter"]: e for e in res["sensitivity"]}
    assert set(sens) == {"direct", "low_floor"}
    assert sens["low_floor"]["n_floor_missing"] == 1
    assert sens["direct"]["n_rows_excluded"] == 3 and sens["low_floor"]["n_rows_excluded"] == 3
    assert sens["direct"]["diff_median_eok"] < res["diff_median_eok"]       # 20억 직거래 3건이 84㎡대 중위를 끌어올렸다
    assert sens["low_floor"]["diff_median_eok"] < res["diff_median_eok"]    # 1억 1층 3건이 59㎡대 중위를 끌어내렸다


def _jeonse(rows):
    return {LAWD: rows, "_done": _done(MONTHS10)}


def _jrow(apt, eok, ctype, area=84.0, **kw):
    x = {"apt": apt, "area": area, "price": int(round(eok * 1e8)), "ym": "202606", "contract_type": ctype, "apt_seq": f"11530-{apt}"}
    x.update(kw)
    return x


def test_d3_deposit_uses_new_contracts_only_and_counts_renewals():
    sale = [_trade("하나", 84.0, 9.0, "202606", i + 1) for i in range(10)]
    j = [_jrow("하나", 5.0, "신규") for _ in range(5)] + [_jrow("하나", 3.0, "갱신") for _ in range(6)]
    ds = {"complexes": [_row("하나", "1")]}
    res = ins.compute_deposit_change(ds, molit=_molit10(sale), win=M10, spec=ins.SPEC_D3, jeonse=_jeonse(j))
    assert res["n"] == 1
    c = res["complexes"][0]
    assert c["jeonse_eok"] == 5.0 and c["jeonse_n"] == 5
    assert res["amount10_median_man"] == 5000 and res["n_renewal_rows_excluded"] == 6


def test_d3_deposit_drops_complex_with_fewer_than_five_new_contracts_and_gates_coverage():
    sale = [_trade("하나", 84.0, 9.0, "202606", i + 1) for i in range(10)]
    j = [_jrow("하나", 5.0, "신규") for _ in range(4)] + [_jrow("하나", 3.0, "갱신") for _ in range(6)]
    ds = {"complexes": [_row("하나", "1")]}
    assert ins.compute_deposit_change(ds, molit=_molit10(sale), win=M10, spec=ins.SPEC_D3, jeonse=_jeonse(j))["n"] == 0
    j2 = [_jrow("하나", 5.0, "신규") for _ in range(5)] + [_jrow("하나", 3.0, None) for _ in range(6)]
    with pytest.raises(ins.SpecNotApplicable, match="contract_type"):
        ins.compute_deposit_change(ds, molit=_molit10(sale), win=M10, spec=ins.SPEC_D3, jeonse=_jeonse(j2))


def test_low_floor_filter_drops_only_floors_one_and_two():
    assert not ins._keep_row({"floor": 1}, "low_floor") and not ins._keep_row({"floor": 2}, "low_floor")
    assert ins._keep_row({"floor": 0}, "low_floor") and ins._keep_row({"floor": -1}, "low_floor")
    assert ins._keep_row({"floor": 3}, "low_floor") and ins._keep_row({"floor": None}, "low_floor")


def test_d3_deposit_holds_complex_whose_sale_and_jeonse_seq_differ():
    sale = [_trade("하나", 84.0, 9.0, "202606", i + 1) for i in range(10)]
    j = [_jrow("하나", 5.0, "신규", apt_seq="11530-동명다른단지") for _ in range(6)]
    ds = {"complexes": [_row("하나", "1")]}
    res = ins.compute_deposit_change(ds, molit=_molit10(sale), win=M10, spec=ins.SPEC_D3, jeonse=_jeonse(j))
    assert res["n"] == 0 and res["seq_hold"] == [["구로", "하나"]]


def test_d3_deposit_ignores_jeonse_rows_outside_window_and_invalid_contract_types():
    sale = [_trade("하나", 84.0, 9.0, "202606", i + 1) for i in range(10)]
    j = [_jrow("하나", 5.0, "신규") for _ in range(5)] + [_jrow("하나", 1.0, "신규", ym="202409") for _ in range(9)]
    ds = {"complexes": [_row("하나", "1")]}
    res = ins.compute_deposit_change(ds, molit=_molit10(sale), win=M10, spec=ins.SPEC_D3, jeonse=_jeonse(j))
    assert res["complexes"][0]["jeonse_eok"] == 5.0 and res["complexes"][0]["jeonse_n"] == 5
    bad = [_jrow("하나", 5.0, "신규") for _ in range(5)] + [_jrow("하나", 3.0, "기타") for _ in range(6)]
    with pytest.raises(ins.SpecNotApplicable, match="contract_type"):
        ins.compute_deposit_change(ds, molit=_molit10(sale), win=M10, spec=ins.SPEC_D3, jeonse=_jeonse(bad))


def test_d3_deposit_requires_complete_jeonse_months():
    sale = [_trade("하나", 84.0, 9.0, "202606", i + 1) for i in range(10)]
    j = {LAWD: [_jrow("하나", 5.0, "신규") for _ in range(5)], "_done": _done(MONTHS10[:-1])}
    with pytest.raises(ValueError):
        ins.compute_deposit_change({"complexes": [_row("하나", "1")]}, molit=_molit10(sale), win=M10, spec=ins.SPEC_D3, jeonse=j)


def _watch_trades(apt, prices, ym, **kw):
    return [_trade(apt, 84.0, p, ym, i + 1, **kw) for i, p in enumerate(prices)]


def test_d3_watch_seq_hold_and_row_filter():
    a, b = M10.watch
    trades = (_watch_trades("유지", [9, 9, 12, 12, 12], a[0]) + _watch_trades("유지", [9, 9, 12, 12, 12], b[0])
              + _watch_trades("섞임", [9, 9, 12, 12, 12], a[0]) + _watch_trades("섞임", [9, 9, 12, 12, 12], b[0])
              + [_trade("섞임", 59.0, 5.0, a[0], 28, apt_seq="11530-다른")])
    ds = {"complexes": [_row("유지", "1"), _row("섞임", "2")]}
    res = ins.compute_budget_watch(ds, _molit10(trades), win=M10, spec=ins.SPEC_D3)
    assert res["n_seq_hold"] == 1 and res["seq_hold"] == [["구로", "섞임"]]
    assert res["cross"]["2+|2+"] == 1
    direct = [_trade("유지", 84.0, 9.0, b[0], 20 + i, dealing="직거래") for i in range(2)]
    t2 = _watch_trades("유지", [9, 9, 12, 12, 12], a[0]) + _watch_trades("유지", [12] * 5, b[0]) + direct
    r_all = ins.compute_budget_watch({"complexes": [_row("유지", "1")]}, _molit10(t2), win=M10, spec=ins.SPEC_D3)
    r_dir = ins.compute_budget_watch({"complexes": [_row("유지", "1")]}, _molit10(t2), win=M10, spec=ins.SPEC_D3, row_filter="direct")
    assert r_all["cross"]["2+|2+"] == 1 and r_dir["cross"]["2+|0"] == 1


def test_d3_grid_appends_filter_rows_after_d2_rows():
    grid = ins.compute_budget_watch_grid({"complexes": []}, _molit10([]), win=M10, spec=ins.SPEC_D3)
    labels = [g["setting"] for g in grid]
    assert labels[-2:] == ["직거래 제외", "1~2층 제외"]
    assert "창 3개월씩(4~6월 vs 7~9월)" in labels and "10월 포함 참고(3~6월 vs 7~10월)" in labels


# ── 판정 ──────────────────────────────────────────────────────────────────

def _ups(n=190, diff=1.55, lo=1.02, hi=4.18, t10=1.6, t20=1.5, split=3):
    bands = [{"band": "6억 미만", "diff_median_eok": lo}] + [{"band": b, "diff_median_eok": 2.0} for b in ("6~8억", "8~10억", "10~15억", "15~20억")]
    bands.append({"band": "20억 이상", "diff_median_eok": hi})
    return {"n": n, "diff_median_eok": diff, "bands": bands, "split_hold": [{}] * split,
            "thresholds": [{"min_n": 5, "diff_median_eok": diff}, {"min_n": 10, "diff_median_eok": t10}, {"min_n": 20, "diff_median_eok": t20}]}


def test_judge_upsize_keep_revise_hold():
    prev = _ups()
    assert se.judge_upsize(prev, _ups(n=180, diff=1.70), split_now=3)["verdict"] == "유지"
    r = se.judge_upsize(prev, _ups(n=180, diff=1.80), split_now=3)
    assert r["verdict"] == "수정" and any("0.20억" in x for x in r["reasons"])
    assert se.judge_upsize(prev, _ups(n=180, lo=4.5), split_now=3)["verdict"] == "수정"          # 금액 순서 깨짐
    assert se.judge_upsize(prev, _ups(n=180, t20=1.9), split_now=3)["verdict"] == "수정"          # 20건 줄이 0.30억 넘게
    assert se.judge_upsize(prev, _ups(n=150), split_now=3)["verdict"] == "보류"                    # 재계산 가능 79%
    assert se.judge_upsize(prev, _ups(n=180), split_now=6)["verdict"] == "보류"                    # 군집 보류 2배


def _dep(n=578, a10=5480, bands70=60, values=None):
    vals = values if values is not None else [a10] * n
    return {"n": len(vals), "amount10_median_man": a10, "bands": [{"band": "70% 이상", "n": bands70}],
            "complexes": [{"gu": "구로", "complex_no": str(i), "amount_man": {0.1: v}} for i, v in enumerate(vals)]}


def test_judge_deposit_keep_revise_hold():
    prev = _dep()
    assert se.judge_deposit(prev, _dep(n=560), held_back=0, cur=_dep(n=300, a10=5600), prev_spec=ins.SPEC, cur_spec=ins.SPEC_D3)["verdict"] == "유지"
    r = se.judge_deposit(prev, _dep(n=560), held_back=0, cur=_dep(n=300, a10=5900), prev_spec=ins.SPEC, cur_spec=ins.SPEC_D3)
    assert r["verdict"] == "수정"                                                                   # 신규만 값이 전체 계약 값과 5% 넘게 다름
    assert se.judge_deposit(prev, _dep(n=510), held_back=0, cur=_dep(n=300), prev_spec=ins.SPEC, cur_spec=ins.SPEC_D3)["verdict"] == "보류"
    assert se.judge_deposit(prev, _dep(n=510), held_back=12, cur=_dep(n=300), prev_spec=ins.SPEC, cur_spec=ins.SPEC_D3)["verdict"] == "유지"
    assert se.judge_deposit(_dep(), _dep(n=560, a10=5480), held_back=0, cur=_dep(n=560, a10=5900),
                            prev_spec=ins.SPEC_D3, cur_spec=ins.SPEC_D3)["verdict"] == "유지"     # 같은 정의끼리는 정의 차 판정 없음
    held = se.judge_deposit(prev, _dep(n=560), held_back=0, cur=_dep(n=200), prev_spec=ins.SPEC, cur_spec=ins.SPEC_D3)
    assert held["verdict"] == "보류" and "신규" in held["reasons"][0]                              # 새 정의 표본이 다리 계산의 50% 미만


def test_judge_deposit_compares_same_complexes_not_attrition():
    bridge = _dep(values=[5000] * 50 + [9000] * 50)                                             # 전체 중위 7,000
    cur = _dep(values=[5000] * 50 + [9000] * 10)                                                # 고가 단지 이탈 — 전체 중위 5,000
    cur["complexes"] = bridge["complexes"][:60]
    r = se.judge_deposit(_dep(n=100), bridge, held_back=0, cur=cur, prev_spec=ins.SPEC, cur_spec=ins.SPEC_D3)
    assert r["verdict"] == "유지"


def _wat(keep=71, lost=5, gained=0, ntb=1920, lost_cells=None):
    cells = {"2+|0": lost_cells if lost_cells is not None else [{"gu": "구로", "complex_no": str(i)} for i in range(lost)]}
    return {"cross": {"2+|2+": keep, "2+|0": lost, "0|2+": gained}, "n_trades": {"A": 2095, "B": ntb}, "cells": cells,
            "panel": [{"gu": "구로", "complex_no": str(i), "status": "2+|0"} for i in range(lost)]}


def test_judge_watch_keep_revise_hold():
    prev = _wat()
    assert se.judge_watch(prev, _wat(lost=6, gained=1, ntb=2000))["verdict"] == "유지"
    assert se.judge_watch(prev, _wat(lost=1, gained=3, ntb=2000))["verdict"] == "수정"            # 뒤집힘
    assert se.judge_watch(prev, _wat(lost=8, gained=0, ntb=2000))["verdict"] == "수정"            # ±2 초과
    again = _wat(lost=2, ntb=2000)
    again["panel"] = [{"gu": "구로", "complex_no": str(i), "status": "2+|2+" if i < 3 else "2+|0"} for i in range(5)]
    assert se.judge_watch(prev, again)["verdict"] == "수정"                                       # 지난 판 (2+,0) 5곳 중 3곳이 B창 거래 얻음
    assert se.judge_watch(prev, _wat(ntb=2113))["verdict"] == "보류"                               # B창 거래 +10% 초과


def test_common_hold_reasons_frame_change():
    prev = {"day": "2026-10-13", "last_month": "202609", "fingerprint": "abc", "panel_rows_by_month": {},
            "frame": {"scope": "25gu", "schema_version": 3}}
    clock = {"last_month": "202610"}
    assert se.common_hold_reasons(D(2026, 11, 10), prev, clock, "abc", {}, frame={"scope": "25gu", "schema_version": 3}) == []
    r = se.common_hold_reasons(D(2026, 11, 10), prev, clock, "abc", {}, frame={"scope": "11gu", "schema_version": 3})
    assert r and "범위" in r[0]


def test_common_hold_reasons():
    prev = {"day": "2026-10-13", "last_month": "202609", "fingerprint": "abc",
            "panel_rows_by_month": {"202607": 1000, "202608": 500, "202609": 300}}
    clock = {"last_month": "202610"}
    assert se.common_hold_reasons(D(2026, 11, 10), prev, clock, "abc", {"202607": 1050, "202608": 460}) == []
    assert se.common_hold_reasons(D(2026, 11, 10), None, clock, "abc", {})
    assert se.common_hold_reasons(D(2026, 11, 10), prev, {"last_month": "202609"}, "abc", {"202607": 1000, "202608": 500})
    assert se.common_hold_reasons(D(2026, 11, 10), prev, clock, "zzz", {"202607": 1000, "202608": 500})
    reasons = se.common_hold_reasons(D(2026, 11, 10), prev, clock, "abc", {"202607": 1000, "202608": 445})   # −11%
    assert reasons and "202608" in reasons[0]
    # 지난 판 마지막 달(202609, 미완결)은 비교하지 않는다
    assert se.common_hold_reasons(D(2026, 11, 10), prev, clock, "abc", {"202607": 1000, "202608": 500, "202609": 900}) == []


# ── 판 기록·보류 흐름 ─────────────────────────────────────────────────────

def _world(tmp_path, *, seq_ok=True):
    """구로 단지 3곳의 12개월 거래 — 10-13 판(D2) 시드 + 11-10 자료."""
    def trades(months, seq=True):
        out = []
        for k, apt in enumerate(("가람", "나래", "다솜")):
            for ym in months:
                for i in range(2):
                    out.append(_trade(apt, 59.0, 8.0 + k + i * 0.01, ym, i + 1, apt_seq=f"s-{apt}" if seq else None))
                    out.append(_trade(apt, 84.0, 9.2 + k + i * 0.01, ym, i + 3, apt_seq=f"s-{apt}" if seq else None))
        return out
    ds = {"complexes": [_row(n, str(i + 1)) for i, n in enumerate(("가람", "나래", "다솜"))], "data_asof": "2026-11-10",
          "takedown": "문의"}
    m13 = ins.months_between(*ins.DEFAULT_WINDOWS.year)
    molit13 = {LAWD: trades(m13), "_done": _done(m13)}
    molit = {LAWD: trades(MONTHS10, seq=seq_ok), "_done": _done(MONTHS10)}
    res13 = ins.build_series_results(ds, molit13, kinds=("upsize",))
    se.seed_edition(str(tmp_path), "upsize", "2026-10-13", res13, ds, molit13, url="https://floker.tistory.com/121")
    return ds, molit


def test_build_edition_writes_draft_and_ledger_then_is_idempotent(tmp_path):
    ds, molit = _world(tmp_path)
    out = se.build_edition(str(tmp_path), D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)
    assert out["status"] == "ready" and out["verdict"] in ("유지", "수정")
    draft = (tmp_path / "report/blog/tistory/2026-11-10-upsize-tistory-draft.html").read_text(encoding="utf-8")
    assert "지난 판과 비교" in draft and "floker.tistory.com/121" in draft and "2026-12-08" in draft
    assert "10월 계약 제외(11개월)" in draft and "2026-11-10에 같은 정의로" not in draft
    assert "지난 판의 마지막 달" in draft
    led = json.loads((tmp_path / "report/blog/series/upsize/2026-11-10.json").read_text(encoding="utf-8"))
    assert led["spec"] == ins.SPEC_D3 and led["last_month"] == "202610" and led["verdict"] == out["verdict"]
    assert led["panel_rows_by_month"] and max(led["panel_rows_by_month"]) == "202609"
    before = (tmp_path / "report/blog/series/upsize/2026-11-10.json").stat().st_mtime_ns
    assert se.build_edition(str(tmp_path), D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)["status"] == "ready"
    assert (tmp_path / "report/blog/series/upsize/2026-11-10.json").stat().st_mtime_ns == before


def test_build_edition_holds_without_draft_and_removes_stale_draft(tmp_path):
    ds, molit = _world(tmp_path, seq_ok=False)
    stale = tmp_path / "report/blog/tistory/2026-11-10-upsize-tistory-draft.html"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text("old", encoding="utf-8")
    out = se.build_edition(str(tmp_path), D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)
    assert out["status"] == "held" and any("apt_seq" in r for r in out["reasons"])
    assert not stale.exists()
    hold = json.loads((tmp_path / "report/blog/series/upsize/2026-11-10.hold.json").read_text(encoding="utf-8"))
    assert hold["reasons"] == out["reasons"]
    again = se.build_edition(str(tmp_path), D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)
    assert again["status"] == "held" and not stale.exists()


def test_build_edition_holds_when_data_month_is_stale(tmp_path):
    ds, _ = _world(tmp_path)
    m = ins.months_between(*ins.DEFAULT_WINDOWS.year)
    molit_old = {LAWD: [_trade("가람", 59.0, 8.0, "202609")], "_done": _done(m)}
    sent = []
    out = se.build_edition(str(tmp_path), D(2026, 11, 10), "upsize", ds=ds, molit=molit_old, jeonse=None, notify=True,
                           today=D(2026, 11, 10), notifier=sent.append)
    assert out["status"] == "wait" and any("202610" in r for r in out["reasons"])
    assert not (tmp_path / "report/blog/series/upsize/2026-11-10.hold.json").exists()
    se.build_edition(str(tmp_path), D(2026, 11, 10), "upsize", ds=ds, molit=molit_old, jeonse=None, notify=True,
                     today=D(2026, 11, 10), notifier=sent.append)
    se.build_edition(str(tmp_path), D(2026, 11, 10), "upsize", ds=ds, molit=molit_old, jeonse=None, notify=True,
                     today=D(2026, 11, 11), notifier=sent.append)
    assert len(sent) == 2                                  # 대기 알림은 하루 1번


def test_previous_edition_is_latest_published_before_day(tmp_path):
    base = tmp_path / "report/blog/series/watch"
    base.mkdir(parents=True)
    for name, url in (("2026-10-13.json", "https://floker.tistory.com/123"), ("2026-11-24.hold.json", None),
                      ("2026-11-25.json", None), ("2026-12-22.json", "https://floker.tistory.com/200")):
        (base / name).write_text(json.dumps({"day": name[:10], "url": url}), encoding="utf-8")
    prev = se.load_prev_edition(str(tmp_path), "watch", D(2026, 12, 22))
    assert prev["day"] == "2026-10-13"                     # 11-25 판은 기록만 있고 발행되지 않았다


# ── 매일 실행(cron) 연결 ──────────────────────────────────────────────────

def test_open_kind_is_due_series_until_published_or_held(tmp_path, monkeypatch):
    monkeypatch.delenv("RE_SERIES_AUTOPUBLISH", raising=False)
    root = str(tmp_path)
    assert se.open_kind(root, D(2026, 11, 3)) is None
    assert se.open_kind(root, D(2026, 11, 10)) == ("upsize", D(2026, 11, 10))
    assert se.open_kind(root, D(2026, 11, 13)) == ("upsize", D(2026, 11, 10))      # 금요일까지 유예
    assert se.open_kind(root, D(2026, 11, 14)) is None
    (tmp_path / ".last-tistory-published-upsize").write_text("2026-11-10\n", encoding="utf-8")
    assert se.open_kind(root, D(2026, 11, 11)) is None
    assert se.open_kind(root, D(2026, 11, 17)) == ("deposit", D(2026, 11, 17))
    hold = tmp_path / "report/blog/series/deposit/2026-11-17.hold.json"
    hold.parent.mkdir(parents=True)
    hold.write_text("{}", encoding="utf-8")
    assert se.open_kind(root, D(2026, 11, 18)) is None


def test_open_kind_closes_after_build_when_autopublish_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_SERIES_AUTOPUBLISH", "0")
    root = str(tmp_path)
    assert se.open_kind(root, D(2026, 11, 24)) == ("watch", D(2026, 11, 24))
    se.draft_path(root, "watch", "2026-11-24").parent.mkdir(parents=True)
    se.draft_path(root, "watch", "2026-11-24").write_text("x", encoding="utf-8")
    se.edition_path(root, "watch", "2026-11-24").parent.mkdir(parents=True)
    se.edition_path(root, "watch", "2026-11-24").write_text(json.dumps(
        {"verdict": "유지", "draft_sha256": se._sha(se.draft_path(root, "watch", "2026-11-24"))}), encoding="utf-8")
    assert se.open_kind(root, D(2026, 11, 24)) is None


def test_publishable_binds_draft_to_judged_edition(tmp_path):
    ds, molit = _world(tmp_path)
    root = str(tmp_path)
    assert se.publishable(root, "upsize", "2026-11-10") == "unjudged"
    se.build_edition(root, D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)
    assert se.publishable(root, "upsize", "2026-11-10") is None
    dp = se.draft_path(root, "upsize", "2026-11-10")
    dp.write_text(dp.read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert se.publishable(root, "upsize", "2026-11-10") == "draft_mismatch"
    again = se.build_edition(root, D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)
    assert again["new"] is True and se.publishable(root, "upsize", "2026-11-10") is None    # 바뀐 원고는 다시 만든다


def test_build_edition_reports_whether_it_wrote_the_edition(tmp_path):
    ds, molit = _world(tmp_path)
    first = se.build_edition(str(tmp_path), D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)
    again = se.build_edition(str(tmp_path), D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)
    assert first["new"] is True and again["new"] is False


def test_cli_build_crash_notifies_and_returns_1(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(se, "_notify", sent.append)
    monkeypatch.setattr(se, "_load_inputs", lambda *a: ({}, {}, None))

    def boom(*a, **k):
        raise RuntimeError("계산 중단")
    monkeypatch.setattr(se, "build_edition", boom)
    assert se.main(["build", "--day", "2026-11-10", "--outroot", str(tmp_path)]) == 1
    assert len(sent) == 1 and "RuntimeError" in sent[0] and "계산 중단" in sent[0]


def test_cli_build_autopublish_off_notifies_once(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(se, "_notify", sent.append)
    monkeypatch.setattr(se, "_load_inputs", lambda *a: ({}, {}, None))
    monkeypatch.setenv("RE_SERIES_AUTOPUBLISH", "0")
    monkeypatch.setattr(se, "preserve_inputs", lambda *a: tmp_path)
    results = iter([True, False])
    monkeypatch.setattr(se, "build_edition", lambda *a, **k: {"status": "ready", "verdict": "유지", "reasons": [],
                                                               "draft": "d.html", "new": next(results)})
    assert se.main(["build", "--day", "2026-11-10", "--outroot", str(tmp_path)]) == 5
    assert se.main(["build", "--day", "2026-11-10", "--outroot", str(tmp_path)]) == 5
    assert len(sent) == 1


def test_preserve_inputs_copies_edition_snapshots(tmp_path):
    root = str(tmp_path)
    sp = se._snapshot_paths(root, "2026-11-18")
    for k, body in (("dataset", b"{}"), ("molit", b"m"), ("jeonse", b"j")):
        sp[k].parent.mkdir(parents=True, exist_ok=True)
        sp[k].write_bytes(body)
    stale = tmp_path / se.INPUTS_DIR / "2026-11-17-deposit" / "dataset-2026-11-17.json.gz"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old")
    got = se.preserve_inputs(root, "deposit", "2026-11-17", "2026-11-18")
    from pathlib import Path
    assert sorted(p.name for p in Path(got["dir"]).iterdir()) == ["dataset-2026-11-18.json.gz", "jeonse-2026-11-18.json.gz",
                                                                  "molit-2026-11-18.json.gz"]
    assert got["jeonse"] == "jeonse-2026-11-18.json.gz"


def test_rebase_recomputes_previous_edition_from_preserved_inputs(tmp_path, monkeypatch):
    ds, molit = _world(tmp_path)
    root = str(tmp_path)
    m13 = ins.months_between(*ins.DEFAULT_WINDOWS.year)
    molit13 = {LAWD: [x for x in molit[LAWD] if x["ym"] in m13], "_done": _done(m13)}
    inp = tmp_path / se.INPUTS_DIR / "2026-10-13-upsize"
    inp.mkdir(parents=True)
    import gzip
    with gzip.open(inp / "dataset-2026-10-13.json.gz", "wt", encoding="utf-8") as f:
        json.dump(ds, f)
    with gzip.open(inp / "molit-2026-10-13.json.gz", "wt", encoding="utf-8") as f:
        json.dump(molit13, f)
    ep = se.edition_path(root, "upsize", "2026-10-13")
    led = json.loads(ep.read_text(encoding="utf-8"))
    led["fingerprint"] = "old"
    ep.write_text(json.dumps(led), encoding="utf-8")
    assert se.build_edition(root, D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)["status"] == "held"
    (tmp_path / "report/blog/series/upsize/2026-11-10.hold.json").unlink()
    new = se.rebase_edition(root, "upsize", "2026-10-13", today=D(2026, 11, 10))
    assert new["fingerprint"] == se.definition_fingerprint() and new["rebased"]["from_fingerprint"] == "old"
    assert new["url"] == "https://floker.tistory.com/121" and (ep.parent / "2026-10-13.json.bak-old").exists()
    out = se.build_edition(root, D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)
    assert out["status"] == "ready"
    assert "다시 낸 값" in se.draft_path(root, "upsize", "2026-11-10").read_text(encoding="utf-8")


# ── 구현 리뷰 R1(Codex) ───────────────────────────────────────────────────

def test_ready_draft_is_not_reused_after_definition_or_scope_change(tmp_path, monkeypatch):
    ds, molit = _world(tmp_path)
    root = str(tmp_path)
    assert se.build_edition(root, D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)["status"] == "ready"
    assert se.reusable(root, "upsize", "2026-11-10")
    monkeypatch.setenv("RE_SCAN_SCOPE", "other")
    assert not se.reusable(root, "upsize", "2026-11-10")
    monkeypatch.delenv("RE_SCAN_SCOPE")
    monkeypatch.setattr(se, "definition_fingerprint", lambda: "changed")
    assert not se.reusable(root, "upsize", "2026-11-10")
    out = se.build_edition(root, D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False)
    assert out["status"] == "held" and not se.draft_path(root, "upsize", "2026-11-10").exists()


def test_fingerprint_covers_calculation_bodies_and_name_rules():
    names = {f.__name__ for f in se._FP_FUNCS}
    assert {"compute_area_upsize", "compute_deposit_change", "compute_budget_watch", "windows_for", "months_between",
            "require_complete_months", "index_named_trades", "canonical_complex_name", "build_name_index"} <= names


def test_watch_gain_counts_b_window_budget_trade_even_when_a_window_is_short():
    m = ins.windows_for("202609")
    (a0, a1), (b0, b1) = m.watch
    ma, mb = ins.months_between(a0, a1), ins.months_between(b0, b1)
    trades = [_trade("가람", 84.0, 8.0, ma[i], 1 + i) for i in range(4)]            # A창 4건 = short
    trades += [_trade("가람", 84.0, 8.0 if i == 0 else 12.0, mb[i % 4], 1 + i) for i in range(6)]
    year = ins.months_between(*m.year)
    molit = {LAWD: trades, "_done": _done(year)}
    ds = {"complexes": [_row("가람", "1")]}
    again = ins.compute_budget_watch(ds, molit, win=m, spec=ins.SPEC)
    entry = again["panel"][0]
    assert entry["status"] == "short_A" and entry["b_le_days"] == 1
    prev = _wat(lost=2, lost_cells=[{"gu": "구로", "complex_no": "1"}, {"gu": "구로", "complex_no": "9"}])
    again["cross"] = {"2+|2+": 71, "2+|0": 5, "0|2+": 0}
    again["n_trades"] = {"A": 2095, "B": 1920}
    assert se.judge_watch(prev, again)["verdict"] == "수정"                         # 2곳 중 1곳이 B창 거래 얻음


def test_upsize_split_count_includes_new_site_held_complexes():
    pool = {("구로", "1"), ("구로", "2")}
    n = se.upsize_split_count(own_split=3, site_held={("구로", "1"), ("강남", "7"), ("강남", "8")}, pool=pool,
                              prev_site_held={("강남", "8")})
    assert n == {"own": 3, "pool": 1, "new": 1, "total": 5}
    assert se.upsize_split_count(own_split=0, site_held={("강남", "7")}, pool=pool, prev_site_held=None)["new"] == 1


def test_rebase_uses_inputs_recorded_in_ledger(tmp_path):
    import gzip
    ds, molit = _world(tmp_path)
    root = str(tmp_path)
    m13 = ins.months_between(*ins.DEFAULT_WINDOWS.year)
    good = {LAWD: [x for x in molit[LAWD] if x["ym"] in m13], "_done": _done(m13)}
    inp = tmp_path / se.INPUTS_DIR / "2026-10-13-upsize"
    inp.mkdir(parents=True)
    for day, m in (("2026-10-12", {LAWD: [], "_done": _done(m13)}), ("2026-10-13", good)):
        with gzip.open(inp / f"dataset-{day}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(ds, f)
        with gzip.open(inp / f"molit-{day}.json.gz", "wt", encoding="utf-8") as f:
            json.dump(m, f)
    new = se.rebase_edition(root, "upsize", "2026-10-13", today=D(2026, 11, 10))
    assert new["result"]["n"] == 3                                                 # 10-12(빈 입력)이 아니라 기록된 10-13


def test_preserve_runs_before_ledger_so_failed_preserve_is_retried(tmp_path):
    ds, molit = _world(tmp_path)
    root = str(tmp_path)

    def broken():
        raise OSError("disk")
    with pytest.raises(OSError):
        se.build_edition(root, D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False, preserve=broken)
    assert not se.edition_path(root, "upsize", "2026-11-10").exists()
    calls = []
    out = se.build_edition(root, D(2026, 11, 10), "upsize", ds=ds, molit=molit, jeonse=None, notify=False,
                           preserve=lambda: calls.append(1) or {"dataset": "dataset-2026-11-10.json.gz"})
    assert out["status"] == "ready" and out["new"] and calls == [1]
    assert json.loads(se.edition_path(root, "upsize", "2026-11-10").read_text(encoding="utf-8"))["inputs"]["dataset"] == "dataset-2026-11-10.json.gz"


def test_cli_missing_snapshot_records_wait_and_notifies_once_a_day(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(se, "_notify", sent.append)
    args = ["build", "--day", "2026-11-10", "--today", "2026-11-10", "--kind", "upsize", "--outroot", str(tmp_path)]
    assert se.main(args) == 4 and se.main(args) == 4
    rec = json.loads(se.wait_path(str(tmp_path), "upsize", "2026-11-10").read_text(encoding="utf-8"))
    assert rec["notified"] == ["2026-11-10"] and "스냅샷" in rec["reasons"][0] and len(sent) == 1


def test_cli_input_read_crash_notifies(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(se, "_notify", sent.append)

    def bad(*a):
        raise ValueError("gzip 손상")
    monkeypatch.setattr(se, "_load_inputs", bad)
    assert se.main(["build", "--day", "2026-11-10", "--kind", "upsize", "--outroot", str(tmp_path)]) == 1
    assert len(sent) == 1 and "gzip 손상" in sent[0]


def test_d3_seq_text_discloses_no_address_check():
    assert "주소" in ins._seq_txt([["구로", "가람"]])
