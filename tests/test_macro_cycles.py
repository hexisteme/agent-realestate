"""S5 사이클리포트·조건부통계 — 인상사이클 절단, n회 중 m회 집계, 미도래/시계열 이전, '일관된 패턴 없음', 페이지·절 문구 가드,
월간결산 '거시 맥락' 절은 site 원문에만(게이트 ④ 전), 실데이터 스냅샷은 완료 사이클 ≥ 3(K7)."""
from __future__ import annotations
import glob
import importlib.util
import json
import pathlib
from datetime import date

import pytest

from blog import macro_cycles as mc
from blog import period_delta as pd
from blog.macro_context import _plain
from blog.wording_guard import assert_lead_wording_ok, assert_wording_ok

CHANGES = [["2000-01-01", 2.0], ["2000-06-01", 2.25], ["2000-09-01", 2.5], ["2001-03-01", 2.25],
           ["2002-01-01", 2.5], ["2002-06-01", 2.75], ["2003-01-01", 2.5], ["2004-01-01", 2.75], ["2004-06-01", 2.5],
           ["2005-10-01", 2.75]]                                        # 완료 3(2000-06·2002-01·2004-01) + 진행 1(2005-10)


def _monthly(start: date, months: int, fn) -> list:
    out, d = [], start
    for i in range(months):
        out.append([d.isoformat(), round(fn(i), 3)])
        d = mc._add_months(d, 1)
    return out


def _snapshot(asof: str, series=None, bok=CHANGES) -> dict:
    ser = series if series is not None else _monthly(date(2000, 1, 1), 72, lambda i: 3.0 + 0.1 * i)
    return {"asof": asof, "indicators": {
        "bok_base": {"code": "bok_base", "label": "한국은행 기준금리", "unit": "%", "freq": "event", "series_kind": "changes",
                     "value": bok[-1][1], "date": bok[-1][0], "since": bok[-1][0], "prev_value": bok[-2][1], "prev_date": bok[-2][0],
                     "since_window_start": False, "source": "한국은행", "url": "https://www.bok.or.kr/x", "series": bok},
        "kr_govt10y_m": {"code": "kr_govt10y_m", "label": "국고채 10년(월평균, OECD 경유)", "unit": "%", "freq": "monthly",
                         "series_kind": "observations", "value": ser[-1][1], "date": ser[-1][0], "since": ser[-1][0],
                         "prev_value": ser[-2][1], "prev_date": ser[-2][0], "since_window_start": False,
                         "source": "FRED X", "url": "https://fred.stlouisfed.org/series/X", "series": ser}}}


def test_detect_hike_cycles_segments_and_ongoing():
    cyc = mc.detect_hike_cycles(CHANGES)
    assert [(c["start"], c["end"], c["n_hikes"], c["total_bp"], c["months"], c["ongoing"]) for c in cyc] == [
        ("2000-06-01", "2000-09-01", 2, 50, 3, False), ("2002-01-01", "2002-06-01", 2, 50, 5, False),
        ("2004-01-01", "2004-01-01", 1, 25, 0, False), ("2005-10-01", "2005-10-01", 1, 25, 0, True)]
    assert cyc[0]["from_pct"] == 2.0 and cyc[0]["first_pct"] == 2.25 and cyc[1]["peak_pct"] == 2.75
    assert mc.detect_hike_cycles([["2000-01-01", 2.0], ["2001-01-01", 1.5]]) == []          # 인하만 → 사이클 없음


def test_conditional_stats_counts_pending_no_base_and_consistency():
    ser = _monthly(date(2000, 1, 1), 72, lambda i: 3.0 + 0.1 * i)                     # 2000-01 ~ 2005-12 단조 상승
    events = ["1999-01-01", "2000-06-01", "2002-01-01", "2004-01-01", "2005-11-01"]
    s = mc.compute_conditional_stats(events, ser, (6, 12))
    assert s[6]["no_base"] == ["1999-01-01"] and s[6]["pending"] == ["2005-11-01"] and s[6]["missing"] == [] and s[6]["n"] == 3
    assert all(abs(r["delta"] - 0.6) < 1e-6 for r in s[6]["rows"]) and abs(s[6]["median_delta"] - 0.6) < 1e-6
    assert s[6]["n_up"] == 3 and s[6]["consistent"] and s[12]["consistent"] and abs(s[12]["median_delta"] - 1.2) < 1e-6
    assert s[6]["bases"]["2005-11-01"] is not None and "1999-01-01" not in s[6]["bases"]
    zig = _monthly(date(2000, 1, 1), 72, lambda i: 3.0 + (0.1 * i if i < 36 else -0.1 * i))   # 2003 부터 하락 → 2004 사이클만 음의 Δ
    z = mc.compute_conditional_stats(["2000-06-01", "2002-01-01", "2004-01-01"], zig, (6,))
    assert z[6]["n"] == 3 and z[6]["n_up"] == 2 and z[6]["n_down"] == 1 and not z[6]["consistent"]
    two = mc.compute_conditional_stats(["2000-06-01", "2002-01-01"], ser, (6,))
    assert two[6]["n"] == 2 and not two[6]["consistent"]                                    # 3회 미만은 부호 전부 같아도 통계 아님


def test_conditional_stats_asof_gate_month_end_and_missing():
    """S5 Codex: 계단 지표·45일 창이 미래 목표일에 값을 돌려주던 결함 → asof 게이트 · 28일 클램프 → 말일 클램프 · 과거 결측은 '관측 없음'."""
    step = [["2026-01-01", 2.0], ["2026-06-01", 2.5]]
    s = mc.compute_conditional_stats(["2026-01-16"], step, (6,), kind="changes", asof="2026-06-01")
    assert s[6]["n"] == 0 and s[6]["pending"] == ["2026-01-16"]                             # 07-16 은 미도래
    s2 = mc.compute_conditional_stats(["2026-01-16"], step, (6,), kind="changes", asof="2026-08-01")
    assert s2[6]["n"] == 1 and s2[6]["rows"][0]["delta"] == 0.5                             # asof 가 지나면 계단값 사용
    assert mc._add_months(date(2020, 1, 30), 6) == date(2020, 7, 30) and mc._add_months(date(2020, 1, 31), 1) == date(2020, 2, 29)
    obs = [["2020-01-01", 2.0], ["2020-06-30", 3.0], ["2020-07-30", 1.0]]
    d = mc.compute_conditional_stats(["2020-01-30"], obs, (6,), asof="2020-12-31")
    assert d[6]["rows"][0]["later"] == 1.0 and d[6]["rows"][0]["delta"] == -1.0             # 목표일 07-30 그대로(28일 클램프 아님)
    gap = mc.compute_conditional_stats(["2000-01-01"], [["2000-01-01", 2.0], ["2001-01-01", 3.0]], (6,), asof="2026-09-07")
    assert gap[6]["n"] == 0 and gap[6]["missing"] == ["2000-01-01"] and gap[6]["pending"] == []   # 과거 결측 = 관측 없음


def test_stat_txt_variants_follow_briefing_contract():
    """브리핑 §4: n<3 또는 부호 불일치 → 횟수·중위 없이 판정 한 줄. 부호 일치일 때만 'n회 중 n회 · 중위 Δ'."""
    assert mc.stat_txt({"n": 0}) == "대조 가능한 완료 사이클 없음"
    assert mc.stat_txt({"n": 2, "n_up": 2, "n_down": 0, "n_flat": 0, "median_delta": 0.5, "consistent": False}) == "완료 사이클 2회(3회 미만) — 통계 없음"
    ok = mc.stat_txt({"n": 3, "n_up": 3, "n_down": 0, "n_flat": 0, "median_delta": 0.5, "consistent": True})
    assert ok == "3회 중 3회 상승, 중위 Δ +0.50%p — 부호 일치"
    t = mc.stat_txt({"n": 4, "n_up": 2, "n_down": 1, "n_flat": 1, "median_delta": -0.25, "consistent": False})
    assert t == "일관된 패턴 없음(완료 사이클 4회 대조)" and "중위" not in t
    for x in (t, ok):
        assert_lead_wording_ok(x, "stat_txt")


def test_cycle_report_and_page_pass_wording_and_mark_missing():
    rep = mc.build_cycle_report(_snapshot("2026-09-07"), "2026-09-07")
    assert rep["n_completed"] == 3 and rep["enough"] and rep["targets"][0]["stats"][6]["n"] == 3 and rep["targets"][0]["stats"][6]["consistent"]
    page = mc.render_cycles_page(rep, "2026-09-07")
    assert "부호 일치" in page and "자료 없음" in page and "MOLIT 2006~" in page and "진행 중(대조 제외)" in page   # 단조 상승 시계열 → 표, 진행 사이클은 표본 밖
    two = _snapshot("2026-09-07", bok=CHANGES[:7])                                          # 완료 2 → 표 없이 판정 한 줄
    page2 = mc.render_cycles_page(mc.build_cycle_report(two, "2026-09-07"), "2026-09-07")
    assert "3회 미만" in page2 and "사이클별 반응 표는 싣지 않는다" in page2 and "+6개월 값" not in page2
    zig = _snapshot("2026-09-07", series=_monthly(date(2000, 1, 1), 72, lambda i: 3.0 + (0.1 * i if i < 36 else -0.1 * i)))
    page3 = mc.render_cycles_page(mc.build_cycle_report(zig, "2026-09-07"), "2026-09-07")
    assert "일관된 패턴 없음(완료 사이클 3회 대조)" in page3 and "+6개월 값" not in page3 and "중위 Δ" not in page3
    assert_wording_ok(page, "cycles"); assert_lead_wording_ok(_plain(page), "cycles")
    assert "OVERHEATED" not in page and "과열" not in page and "침체" not in page
    empty = mc.render_cycles_page(None, "2026-09-07")
    assert "미수집" in empty and "<table" not in empty
    assert mc.build_cycle_report({"asof": "2026-09-07", "indicators": {}}, "2026-09-07") is None
    nosrc = _snapshot("2026-09-07"); del nosrc["indicators"]["kr_govt10y_m"]
    assert "수집 실패" in mc.build_cycle_report(nosrc, "2026-09-07")["targets"][0]["missing"]


def test_regime_section_facts_only_and_stale_none():
    sec = mc.build_macro_regime_section(_snapshot("2026-09-06"), "2026-09-07")
    assert sec and sec["lines"][0].startswith("기준금리 2.75%(2005-10-01 변경) — 진행 중인 인상사이클: 시작 2005-10-01(2.50%→2.75%), 인상 1회·누적 +25bp")
    assert any("국고채 10년" in x for x in sec["lines"]) is False        # 2005-12 관측은 2026 기준 100일 초과 → 카드 없음(오래된 값 미표기)
    assert sec["stat_lines"] and "과거 인상사이클 시작 6개월 후 국고채 10년(월평균)" in sec["stat_lines"][0] and sec["n_cycles"] == 3
    assert sec["note_line"] == mc.NOTE_ONE_LINER and [s["source"] for s in sec["sources"]] == ["한국은행", "FRED X"] and "~" in sec["sources"][1]["date"]
    assert mc.build_macro_regime_section(_snapshot("2026-09-01"), "2026-09-07") is None    # 3일 초과
    assert mc.build_macro_regime_section(None, "2026-09-07") is None
    ended = _snapshot("2026-09-06", bok=CHANGES + [["2006-01-01", 2.5]])
    assert "진행 중인 인상사이클 없음(마지막 사이클 2005-10-01~2005-10-01" in mc.build_macro_regime_section(ended, "2026-09-07")["lines"][0]


@pytest.mark.parametrize("flag", [False, True])
def test_monthly_post_macro_section_both_flag_states_and_claim(tmp_path, monkeypatch, flag):
    """게이트 ④: False 면 site 원문에만, True(2026-09-07 기본값) 면 티스토리 원고에도 — 두 상태 모두 출처·해석 제한 줄 포함(Codex 판정)."""
    spec = importlib.util.spec_from_file_location("tpd", pathlib.Path("tests/test_period_delta.py"))
    tpd = importlib.util.module_from_spec(spec); spec.loader.exec_module(tpd)
    monkeypatch.setenv("RE_PERIODIC_POSTS", "monthly")
    monkeypatch.setattr(pd, "MACRO_SECTION_TISTORY", flag)
    snap = tmp_path / "snapshots"; snap.mkdir()
    for dt in ("2026-09-06", "2026-09-13", "2026-09-20"):
        (snap / f"dataset-{dt}.json").write_text(json.dumps(tpd._ds([tpd._row("강남", "A", 9.9)], gen=dt), ensure_ascii=False), encoding="utf-8")
    res = pd.write_period_post(tpd._ds([tpd._row("강남", "A", 10.1)], gen="2026-09-27"), "2026-09-27", str(tmp_path),
                               macro_snapshot=_snapshot("2026-09-26"))
    assert res["kind"] == "monthly"
    site = (tmp_path / "posts" / "2026-09-27-월간결산.html").read_text(encoding="utf-8")
    draft = (tmp_path / "tistory" / "2026-09-27-periodic-tistory-draft.html").read_text(encoding="utf-8")
    assert "거시 맥락" in site and "진행 중인 인상사이클" in site and "../cycles.html" in site
    assert "출처: " in site and "https://www.bok.or.kr/x" in site and "https://fred.stlouisfed.org/series/X" in site and "통계적 유의성·확률이 아님" in site
    assert ("거시 맥락" in draft) is flag and ("인상사이클" in draft) is flag           # False: 티스토리 원고 제외 / True: 포함
    assert ("출처: " in draft) is flag and ("https://fred.stlouisfed.org/series/X" in draft) is flag
    claims = [json.loads(x) for x in (tmp_path / "posts" / "2026-09-27-월간결산.claims.jsonl").read_text(encoding="utf-8").splitlines()]
    m = [c for c in claims if c["claim"] == "monthly_macro_context"]
    assert len(m) == 1 and m[0]["site_only"] is (not flag) and m[0]["n_cycles"] == 3
    assert [s["url"] for s in m[0]["sources"]] == ["https://www.bok.or.kr/x", "https://fred.stlouisfed.org/series/X"]
    assert pd.write_period_post(tpd._ds([tpd._row("강남", "A", 10.1)], gen="2026-09-27"), "2026-09-27", str(tmp_path))["kind"] == "monthly"   # 스냅샷 없어도 발행


def test_real_snapshot_has_three_or_more_completed_cycles():
    files = sorted(glob.glob("report/blog/snapshots/macro/macro-*.json") + glob.glob("report/blog/preview/snapshots/macro/macro-*.json"))
    if not files:
        pytest.skip("실 macro 스냅샷 없음(CI)")
    snap = json.load(open(files[-1], encoding="utf-8"))
    rep = mc.build_cycle_report(snap, snap["asof"])
    assert rep and rep["n_completed"] >= mc.MIN_CYCLES and rep["enough"]                    # K7
    assert rep["cycles"][0]["start"] >= rep["bok_first"]
    page = mc.render_cycles_page(rep, snap["asof"])
    assert ("부호 일치" in page or "일관된 패턴 없음" in page) and "자료 없음" in page
    broken = json.loads(json.dumps(snap)); del broken["indicators"]["kr_govt10y_m"]["unit"]
    assert mc.build_cycle_report(broken, snap["asof"])["targets"][0]["unit"] == ""         # 손상 스냅샷도 KeyError 없이(S5 Codex)
    no_asof = json.loads(json.dumps(snap)); del no_asof["asof"]
    rep2 = mc.build_cycle_report(no_asof, snap["asof"])                                   # asof 결측 → 기준금리 마지막 변경일 폴백(S6 Codex F7)
    assert rep2["asof"] == no_asof["indicators"]["bok_base"]["date"] and mc.render_cycles_page(rep2, snap["asof"])
    bare = json.loads(json.dumps(snap)); del bare["asof"]; del bare["indicators"]["bok_base"]["date"]; bare["indicators"]["bok_base"]["value"] = None
    rep3 = mc.build_cycle_report(bare, snap["asof"])                                      # date·value·asof 전부 결측 → 시계열 마지막 변경(S6b Codex N2)
    last = max(bare["indicators"]["bok_base"]["series"])
    assert rep3["asof"] == last[0] and rep3["bok_last"] == last[0] and rep3["bok_value"] == last[1]
    empty = json.loads(json.dumps(snap)); empty["indicators"]["bok_base"]["value"] = ""; empty["indicators"]["bok_base"]["prev_value"] = ""
    assert mc.build_cycle_report(empty, snap["asof"])["bok_value"] == last[1]                   # 빈 문자열 값도 폴백(S6c Codex R2)
    sec = mc.build_macro_regime_section(empty, snap["asof"])                                    # 월간 거시 절이 예외로 탈락하지 않는다
    assert sec and f"기준금리 {last[1]:.2f}%" in json.dumps(sec, ensure_ascii=False) and "None" not in json.dumps(sec, ensure_ascii=False)
    none_date = json.loads(json.dumps(snap)); none_date["indicators"]["bok_base"]["date"] = None
    assert "None" not in mc.render_cycles_page(mc.build_cycle_report(none_date, snap["asof"]), snap["asof"])
    for bad in (None, "", "2026-13-01", "del"):                                               # 날짜 결측·None·빈값·형식 오류 → 카드 생략, 절·머리줄 유지(S6d Codex R2)
        s2 = json.loads(json.dumps(snap)); ind = s2["indicators"]["bok_base"]
        if bad == "del":
            del ind["date"]
        else:
            ind["date"] = bad
        sec2 = mc.build_macro_regime_section(s2, snap["asof"])
        txt = json.dumps(sec2, ensure_ascii=False)
        assert f"기준금리 {last[1]:.2f}%({last[0]} 변경)" in txt and "None" not in txt and "%()" not in txt
        assert "한국은행 기준금리" not in " ".join(sec2["lines"])                                 # 카드 줄은 생략
        assert sec2["sources"][0]["url"] == snap["indicators"]["bok_base"]["url"] and sec2["sources"][0]["date"] == last[0]   # 출처는 유지(S7 Codex)


def test_ongoing_cycle_is_not_a_completed_sample_and_gaps_are_labelled():
    """S6 Codex F2 — 완료 2 + 진행 1 이면 표본 n=2(진행 사이클의 +h 개월이 지났어도 대조 제외) → 표 없음.
    F5 — 시계열 안 시작 관측 공백은 '시작 관측 없음', 시계열 이전만 '시계열 이전'."""
    snap = _snapshot("2009-12-01", series=_monthly(date(2000, 1, 1), 120, lambda i: 3.0 + 0.1 * i), bok=CHANGES[:8])   # 완료 2000-06·2002-01, 진행 2004-01
    rep = mc.build_cycle_report(snap, "2009-12-01")
    assert rep["n_completed"] == 2 and not rep["enough"] and rep["targets"][0]["stats"][6]["n"] == 2 and not rep["targets"][0]["stats"][6]["consistent"]
    page = mc.render_cycles_page(rep, "2009-12-01")
    assert "3회 미만" in page and "+6개월 값" not in page and "3회 중 3회" not in page
    gap = [["1999-01-01", 1.0], ["2000-07-01", 2.0]] + [[f"{y}-{m}-01", v] for y in (2001, 2002, 2003, 2004, 2005) for m, v in (("01", 1.0), ("07", 2.0))]
    bok = [["1996-01-01", 2.0], ["1996-06-01", 2.25], ["1997-01-01", 2.0], ["2000-01-01", 2.25], ["2000-06-01", 2.0], ["2001-01-01", 2.25],
           ["2001-06-01", 2.0], ["2002-01-01", 2.25], ["2002-06-01", 2.0], ["2003-01-01", 2.25], ["2003-06-01", 2.0]]   # 사이클 1996-06(시계열 이전)·2000-01(시작 관측 공백)·2001~2003
    rep2 = mc.build_cycle_report(_snapshot("2006-01-01", series=gap, bok=bok), "2006-01-01")
    s6 = rep2["targets"][0]["stats"][6]
    assert s6["consistent"] and s6["n"] == 3 and set(s6["no_base"]) == {"1996-06-01", "2000-01-01"}
    page2 = mc.render_cycles_page(rep2, "2006-01-01")
    assert "시작 관측 없음" in page2 and "시계열 이전" in page2
    assert_wording_ok(page, "cycles:real"); assert_lead_wording_ok(_plain(page), "cycles:real")


def test_january_monthly_post_titles_annual_cycle_report(tmp_path, monkeypatch):
    """계획 §0: 매년 1월 월간결산의 거시 절 제목 = '거시 맥락 · 연간 사이클 리포트'(그 외 달은 '거시 맥락'). 2027-01-31 = 1월 마지막 일요일."""
    spec = importlib.util.spec_from_file_location("tpd", pathlib.Path("tests/test_period_delta.py"))
    tpd = importlib.util.module_from_spec(spec); spec.loader.exec_module(tpd)
    monkeypatch.setenv("RE_PERIODIC_POSTS", "monthly")
    snap = tmp_path / "snapshots"; snap.mkdir()
    for dt in ("2026-12-27", "2027-01-03", "2027-01-10", "2027-01-17", "2027-01-24"):                 # 기준 = 전월 마지막 일요일 12-27
        (snap / f"dataset-{dt}.json").write_text(json.dumps(tpd._ds([tpd._row("강남", "A", 9.9)], gen=dt), ensure_ascii=False), encoding="utf-8")
    res = pd.write_period_post(tpd._ds([tpd._row("강남", "A", 10.1)], gen="2027-01-31"), "2027-01-31", str(tmp_path),
                               macro_snapshot=_snapshot("2027-01-30"))
    assert res["kind"] == "monthly"
    site = (tmp_path / "posts" / "2027-01-31-월간결산.html").read_text(encoding="utf-8")
    assert "거시 맥락 · 연간 사이클 리포트" in site and "../cycles.html" in site


def test_section_sources_survive_bok_fallback_and_include_fed_range_lower_bound():
    """S7 Codex ④: 기준금리 카드가 탈락해도(값 None → 시계열 폴백 문장) 한국은행 출처가 남고, 연준 범위를 표시하면 하단 출처도 같이 남는다."""
    snap = _snapshot("2026-09-06")
    snap["indicators"]["bok_base"]["value"] = None
    sec = mc.build_macro_regime_section(snap, "2026-09-07")
    assert "기준금리 2.75%" in sec["lines"][0] and sec["sources"][0]["url"] == "https://www.bok.or.kr/x" and sec["sources"][0]["date"] == "2005-10-01"
    snap = _snapshot("2026-09-06")
    for code, lab, v in (("fed_target_hi", "미 연방기금 목표범위 상단", 3.75), ("fed_target_lo", "미 연방기금 목표범위 하단", 3.5)):
        snap["indicators"][code] = {"code": code, "label": lab, "unit": "%", "freq": "daily", "series_kind": "observations", "value": v,
                                    "date": "2026-09-06", "since": "2026-09-06", "since_window_start": False, "prev_value": v + 0.25,
                                    "prev_date": "2025-12-10", "source": f"FRED {code}", "url": f"https://fred.stlouisfed.org/series/{code}",
                                    "series": [["2026-09-06", v]]}
    sec = mc.build_macro_regime_section(snap, "2026-09-07")
    assert any("미 연방기금 목표범위 3.50~3.75%" in x for x in sec["lines"])
    assert [s["url"] for s in sec["sources"]] == ["https://www.bok.or.kr/x", "https://fred.stlouisfed.org/series/fed_target_hi",
                                                  "https://fred.stlouisfed.org/series/fed_target_lo", "https://fred.stlouisfed.org/series/X"]
