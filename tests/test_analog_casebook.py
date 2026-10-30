"""닮은국면사례집(AnalogCasebook) — PREREG v2 규칙 고정(v1 은 부록). DIKW 검토 2026-10-30 P3.

R2 = Codex 2라운드 REVISE·agy 2라운드 FAIL 에서 채택한 반증(docs/analog-casebook-prereg-v2.md §v1 에서 찾은 문제).
"""
import dataclasses
import json
import math
import re

import pytest

from blog import analog_casebook as ac


def _months(start: str, n: int) -> list[str]:
    return [ac.shift_month(start, i) for i in range(n)]


def _raw(n: int = 240, start: str = "2004-01"):
    """합성 입력 — 축마다 주기가 다른 사인파(결정론). 가격 구판은 끝 24개월 전까지, 신판은 그 뒤 겹침 30개월 포함."""
    ms = _months(start, n)
    price = {m: 100 * (1.004 ** i) * (1 + 0.05 * math.sin(i / 9)) for i, m in enumerate(ms)}
    old = {m: v for m, v in price.items() if m <= ms[n - 25]}
    new = {m: v / 1.13 for m, v in price.items() if m >= ms[n - 55]}
    ytd, run = {}, 0.0
    for i, m in enumerate(ms):
        run = (0.0 if m.endswith("-01") else run) + 1000 + 300 * math.sin(i / 7)
        ytd[m] = run
    return {"price_old": old, "price_new": new,
            "mortgage_rate": {m: 4 + math.sin(i / 11) for i, m in enumerate(ms)},
            "govt3y": {m: 3 + math.sin(i / 13) for i, m in enumerate(ms)},
            "m2": {m: 1000 * (1.006 ** i) * (1 + 0.02 * math.sin(i / 8)) for i, m in enumerate(ms)},
            "seoul_loans": {m: 500 * (1.005 ** i) * (1 + 0.03 * math.sin(i / 10)) for i, m in enumerate(ms[:-1])},
            "permits_ytd": {m: v for m, v in ytd.items() if m not in ms[-1:]}}


def test_chain_joins_new_after_old_end_with_mean_overlap_ratio_and_refuses_wide_spread():
    old = {m: 100.0 + i for i, m in enumerate(_months("2020-01", 30))}
    new = {m: (112.0 + i) / 1.13 for i, m in enumerate(_months("2021-01", 30))}
    out, info = ac.chain_price_index(old, new)
    assert info["n_overlap"] == 18 and info["joined_from"] == "2022-07" and abs(info["factor"] - 1.13) < 1e-12
    assert out["2022-06"] == old["2022-06"] and abs(out["2023-06"] - (100.0 + 41)) < 1e-9
    bent = dict(new)
    bent["2021-03"] *= 1.02
    with pytest.raises(ac.ChainRefused):
        ac.chain_price_index(old, bent)
    with pytest.raises(ac.ChainRefused):
        ac.chain_price_index(old, {m: v for m, v in new.items() if m >= "2022-03"})


def test_ytd_permits_become_monthly_values_and_gaps_stay_empty():
    ytd = {"2024-01": 10.0, "2024-02": 25.0, "2024-03": 45.0, "2024-05": 90.0, "2025-01": 7.0}
    assert ac.monthly_from_ytd(ytd) == {"2024-01": 10.0, "2024-02": 15.0, "2024-03": 20.0, "2025-01": 7.0}


def test_band_uses_inclusive_terciles_of_window_including_value_and_needs_60():
    window = list(range(1, 61))
    q1, q2 = __import__("statistics").quantiles(window, n=3, method="inclusive")
    assert ac.classify_axis_band(window, q1)[0] == "낮음" and ac.classify_axis_band(window, q2)[0] == "중간"
    assert ac.classify_axis_band(window, 60)[0] == "높음"
    assert ac.classify_axis_band(window[:59], 30) is None


def test_judge_month_reads_observation_month_minus_publication_lag():
    axes, _ = ac.prepare_inputs(_raw())
    t = "2020-06"
    bands = ac.judge_month_bands(axes, t)
    for a, b in zip(ac.AXES, bands):
        assert b is None or b["obs"] == ac.shift_month(t, -a.lag)


def test_current_month_is_min_of_last_observation_plus_lag():
    axes, _ = ac.prepare_inputs(_raw())
    want = min(max(axes[a.key]) for a in ac.AXES)   # 비교용 하한
    cur = ac.current_judge_month(axes)
    assert all(ac.shift_month(cur, -a.lag) <= max(axes[a.key]) for a in ac.AXES) and cur >= want


def _rule_bands():
    """현재 2020-01. 가격 흐름(A1)은 band·value, 나머지는 band 만."""
    b = lambda name, v=1.0: {"band": name, "value": v}
    low5 = [b("낮음")] * 5
    return {
        "2020-01": [b("높음", 5.0)] + low5,
        "2015-09": [b("중간", 4.0)] + low5,                          # A1 구간 다름
        "2015-10": [b("높음", 4.0)] + [b("낮음")] * 4 + [None],      # 판정 불가 축
        "2015-11": [b("높음", 0.2)] + low5,                          # A1 구간 같고 방향 보합
        "2015-12": [b("높음", 4.0)] + [b("낮음")] * 4 + [b("높음")],  # A1 같음 + 나머지 4
        "2016-06": [b("높음", 4.0)] + [b("낮음")] * 4 + [b("높음")],  # 간격 43개월
    }


def test_v2_rule_needs_a1_band_and_direction_all_six_axes_and_48_month_gap():
    """R2: 대칭 투표가 가격 흐름 반대 달을 닮았다고 함 · 판정 불가 축 섞임 · 현재 국면 자기 매칭."""
    bands = _rule_bands()
    assert ac.RULE_MAIN == ac.MatchRule(48, 4) and len(ac.RULE_GRID) == 9 and ac.RULE_MAIN in ac.RULE_GRID
    assert ac.candidate_months(bands, "2020-01", ac.RULE_MAIN) == ["2015-09", "2015-11", "2015-12"]
    assert ac.find_analog_months(bands, "2020-01", ac.RULE_MAIN) == ["2015-12"]
    assert ac.find_analog_months(bands, "2020-01", ac.MatchRule(36, 4)) == ["2015-12", "2016-06"]
    assert ac.find_analog_months(bands, "2020-01", ac.MatchRule(48, 5)) == []


def test_base_rate_uses_the_same_population_as_the_rule():
    """R2: 비교 기준은 사례와 같은 모집단(6축 판정 가능·간격) — '아무 달' 아님."""
    price = {m: 100.0 + i for i, m in enumerate(_months("2015-01", 36))}
    base = ac.compute_base_rate(_rule_bands(), price, "2020-01", 12, ac.RULE_MAIN)
    assert (base["n"], base["up"], base["from"], base["to"]) == (3, 3, "2015-09", "2015-12")


def test_nonoverlapping_cases_keep_36_months_between_representatives():
    months = ["2010-01", "2010-02", "2012-12", "2013-01", "2013-02", "2016-02"]
    cases = ac.group_nonoverlapping_cases(months)
    assert [c["rep"] for c in cases] == ["2010-01", "2013-01", "2016-02"]
    assert cases[0]["members"] == ["2010-01", "2010-02", "2012-12"]


def test_outcome_is_pending_after_last_price_month_and_missing_inside_it():
    """R2: 끝 달이 마지막 가격 관측월 뒤면 미도래, 그 안에서 값이 없으면 결측."""
    price = {m: 100.0 + i for i, m in enumerate(_months("2020-01", 20))}
    assert ac.outcome(price, "2020-02", 12) == ("값", pytest.approx((112 / 100 - 1) * 100))
    assert ac.outcome(price, "2020-10", 12) == ("미도래", None)
    holed = {m: v for m, v in price.items() if m not in ("2020-06", "2021-02")}
    assert ac.outcome(holed, "2020-07", 12) == ("결측", None) and ac.outcome(holed, "2020-03", 12) == ("결측", None)
    assert [ac.direction(v) for v in (0.6, 0.5, -0.5, -0.6)] == ["상승", "보합", "보합", "하락"]


_NO_STATS = {"n": 0, "up": 0, "down": 0, "flat": 0, "median": None, "q1": None, "q3": None}


def _force(book, cases, stats12):
    """주 규칙 결과만 바꾼 사본 — 격자 안의 주 규칙도 같은 객체로 바꾼다(36개월은 전부 미도래)."""
    main = dataclasses.replace(book.main, cases=cases, stats={12: stats12, 36: _NO_STATS})
    grid = [main if v.rule == ac.RULE_MAIN else v for v in book.grid]
    return dataclasses.replace(book, main=main, grid=grid, loo=[])


def _fake_cases(book, n):
    return [{"rep": ac.shift_month("2010-01", 40 * i), "members": [ac.shift_month("2010-01", 40 * i)],
             "bands": book.current_bands, "outcomes": {12: ("값", 3.0), 36: ("미도래", None)}} for i in range(n)]


def _text(page: str) -> str:
    return re.sub(r"<style.*?</style>|<script.*?</script>|<[^>]+>", " ", page, flags=re.S)


def test_counts_are_withheld_below_five_confirmed_cases_in_every_variant():
    """R2: n<5 게이트는 첫 문장만이 아니라 모든 변형·지평에 — 사례만 적는다."""
    book = ac.build_analog_casebook(_raw(), "2026-10-30")
    few = _force(book, _fake_cases(book, 4), {"n": 4, "up": 4, "down": 0, "flat": 0, "median": 3.0, "q1": 3.0, "q3": 3.0})
    s = ac.casebook_sentence(few)
    assert "줄여 말하지 않고" in s and "건에서" not in s
    assert not re.search(r"\d+건 중 오름", _text(ac.render_analogs_page(few, "2026-10-30")))
    many = _force(book, _fake_cases(book, 5), {"n": 5, "up": 5, "down": 0, "flat": 0, "median": 3.0, "q1": 3.0, "q3": 3.0})
    s = ac.casebook_sentence(many)
    assert "5건(12개월 뒤 값이 확인된 것) 중 5건에서" in s and "독립이 아닙니다" in s
    assert "5건 중 오름 5" in _text(ac.render_analogs_page(many, "2026-10-30"))
    none = _force(book, [], {"n": 0, "up": 0, "down": 0, "flat": 0, "median": None, "q1": None, "q3": None})
    assert "닮은 달은 없었습니다" in ac.casebook_sentence(none)


def test_page_leads_with_base_rate_and_lists_nine_variants_v1_appendix_and_disclosures():
    book = ac.build_analog_casebook(_raw(), "2026-10-30")
    text = _text(ac.render_analogs_page(book, "2026-10-30"))
    for word in ("확률", "전망", "예상", "추천", "급등", "급락", "아무 달"):
        assert word not in text, word
    for need in (ac.DISCLAIMER, "탐색적 사례집", "규칙을 바꾸면", "부록 — 첫 계산(v1) 결과", "작성자 연쇄", ac.PREREG_PATH,
                 ac.PREREG_V1_PATH, "지금 이후의 방향을 말하지 않으며", "정책 대출"):
        assert need in text, need
    assert len(book.grid) == 9 and text.count("(주 규칙)") == 1
    assert text.index("지금 이후의 방향을 말하지 않으며") < text.index("미리 정한 규칙")
    v1 = ac.load_v1_result()
    assert text.count("묶인 달") == (len(book.main.cases) + sum(len(v.cases) for v in book.grid)
                                    + sum(len(cs) for cs in v1["cases"].values()))


def test_limits_name_only_the_eras_before_the_first_comparable_month():
    book = ac.build_analog_casebook(_raw(), "2026-10-30")
    at = lambda first: ac.limits_text(dataclasses.replace(book, first_judgeable=first))
    assert all(e in at("2015-01") for e in ("2000년대 중반 상승기", "2008년 금융위기 뒤 하락기", "2010~2013년 침체기"))
    assert "2000년대 중반 상승기" in at("2009-01") and "2008년 금융위기" not in at("2009-01")
    assert "비교 대상에 들지 않습니다" not in at("2005-01")


# ── R3 머지 전 리뷰(Codex REVISE 8건·agy REVISE) 채택분 ─────────────────────────────────────────

def test_r3_codex1_grid_lists_members_and_both_horizons_with_per_horizon_gate():
    book = ac.build_analog_casebook(_raw(), "2026-10-30")
    many = _force(book, _fake_cases(book, 5), {"n": 5, "up": 5, "down": 0, "flat": 0, "median": 3.0, "q1": 3.0, "q3": 3.0})
    page = ac.render_analogs_page(many, "2026-10-30")
    grid = page[page.index("규칙을 바꾸면"):page.index("부록 — 첫 계산")]
    others = sum(len(v.cases) for v in many.grid if v.rule != ac.RULE_MAIN)
    assert "<th>36개월 뒤</th>" in grid and grid.count("묶인 달") == 5 + others
    assert "5건 중 오름 5" in grid and "2010년 1월 미도래" in grid   # 12개월은 요약, 36개월은 사례마다


def test_r3_codex2_leave_one_out_needs_five_after_dropping():
    case = lambda i, v: {"rep": ac.shift_month("2010-01", 40 * i), "outcomes": {12: ("값", v), 36: ("미도래", None)}}
    assert ac.leave_one_out([case(i, 1.0) for i in range(5)]) == []
    six = ac.leave_one_out([case(i, 1.0) for i in range(6)])
    assert len(six) == 6 and all(r["n"] == 5 for r in six)


def test_r3_codex3_v1_appendix_is_the_frozen_first_result_not_a_recompute():
    v1 = ac.load_v1_result()
    assert (v1["asof"], v1["code"], v1["current"]) == ("2026-10-30", "f9428dc", "2026-10")
    assert [c["rep"] for c in v1["cases"]["5"]] == ["2023-04", "2026-04"]
    assert v1["cases"]["5"][0]["outcomes"] == {"12": 1.0764, "36": 18.1646}
    book = ac.build_analog_casebook(_raw(), "2026-10-30")   # 합성 입력이어도 부록은 고정본
    text = _text(ac.render_analogs_page(book, "2026-10-30"))
    assert "2026-10-30 계산 고정" in text and "+1.1% 상승" in text and "+18.2% 상승" in text
    bent = json.loads(json.dumps(v1))
    bent["cases"]["5"][0]["outcomes"]["12"] = 1.6
    assert "+1.6% 상승" in _text(ac.render_analogs_page(book, "2026-10-30", v1=bent))


def test_r3_codex4_exact_half_percent_is_flat_despite_float_error():
    assert ac.direction(-0.5000000000000004) == "보합" and ac.direction(0.5000000000000004) == "보합"
    state, pct = ac.outcome({"2020-01": 100.0, "2021-01": 99.5}, "2020-02", 12)
    assert state == "값" and ac.direction(pct) == "보합"


def test_r3_codex5_current_month_steps_back_when_an_axis_has_a_hole_at_the_upper_bound():
    raw = _raw()
    upper = ac.current_judge_month(ac.prepare_inputs(raw)[0])
    hole = ac.shift_month(upper, -2)                       # M2 는 시차 2 — 상한 달의 관측월
    raw["m2"] = {m: v for m, v in raw["m2"].items() if m != hole}
    assert ac.current_judge_month(ac.prepare_inputs(raw)[0]) == upper
    book = ac.build_analog_casebook(raw, "2026-10-30")
    assert book.current == ac.shift_month(upper, -1) and all(b is not None for b in book.current_bands)


def test_r3_codex6_agy2_lead_says_all_months_not_analogs_and_not_a_buy_basis():
    book = dataclasses.replace(ac.build_analog_casebook(_raw(), "2026-10-30"), first_judgeable="2015-01")
    s = ac.base_rate_sentence(book)
    assert s is not None
    for need in ("결과까지 확인된 달", "지금과 닮은 달을 고른 것이 아니라", "2010~2013년 침체기는 이 기간에 들지 않습니다",
                 "매수·매도 판단의 근거로 검증된 자료가 아닙니다"):
        assert need in s, need


def test_r3_codex7_agy1_base_rate_period_comes_from_confirmed_months():
    price = {m: 100.0 + i for i, m in enumerate(_months("2015-01", 36)) if m != "2016-08"}   # 2015-09 의 12개월 끝 달 결측
    base = ac.compute_base_rate(_rule_bands(), price, "2020-01", 12, ac.RULE_MAIN)
    assert (base["n"], base["from"], base["to"]) == (2, "2015-11", "2015-12")


def test_r3_codex8_case_table_shows_each_months_band_bounds():
    b = {"band": "높음", "value": 4.0, "q1": 1.0, "q2": 2.0}
    row = ac._case_table([{"rep": "2015-12", "members": ["2015-12"], "bands": [b] * 6,
                           "outcomes": {12: ("값", 1.0), 36: ("결측", None)}}])
    assert "경계 1.00%/2.00%" in row and "결측" in row


def test_r3_agy3_page_guard_blocks_causal_words():
    assert {"덕분에", "영향으로", "때문에"} <= set(ac._EXTRA_WORDS)


def test_r4_codex_lead_does_not_claim_every_month_when_one_drops_out():
    """R4: 중간 결측으로 빠진 달이 있으면 '그 기간의 모든 달'은 거짓 — 확인된 달 전부라고만 쓴다."""
    raw = _raw()
    book = ac.build_analog_casebook(raw, "2026-10-30")
    hole = ac.shift_month(book.main.base[12]["from"], 30)
    raw["mortgage_rate"] = {m: v for m, v in raw["mortgage_rate"].items() if m != ac.shift_month(hole, -1)}
    holed = ac.build_analog_casebook(raw, "2026-10-30")
    b = holed.main.base[12]
    assert ac._m(b["to"]) - ac._m(b["from"]) + 1 > b["n"]                 # 기간 안에 빠진 달이 있다
    s = ac.base_rate_sentence(holed)
    assert "그 기간의 모든 달" not in s and "6개 지표와 결과가 모두 확인된 달 전부" in s


def test_build_is_deterministic():
    raw = _raw()
    assert ac.render_analogs_page(ac.build_analog_casebook(raw, "2026-10-30"), "2026-10-30") == \
        ac.render_analogs_page(ac.build_analog_casebook(raw, "2026-10-30"), "2026-10-30")


def test_empty_page_when_inputs_missing():
    assert "비어 있습니다" in ac.render_analogs_page(None, "2026-10-30")


def test_site_writes_analogs_page_and_lists_it_only_when_a_casebook_exists(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    import test_sitemap_index as tsi
    from agent_realestate.collectors import analog_inputs
    monkeypatch.setattr(analog_inputs, "load_analog_inputs_latest", lambda dir: {"asof": "2026-10-30", "series": _raw()})
    site_dir, _ = tsi._build_fixture(tmp_path, monkeypatch)
    assert "닮은 사례 전부" in (site_dir / "analogs.html").read_text(encoding="utf-8")
    assert any(loc.endswith("/analogs.html") for loc in tsi._urlset_locs(site_dir, "sitemap-core.xml"))


def test_collector_asks_ecos_for_every_series_and_refuses_partial_vintages(tmp_path):
    from agent_realestate.collectors import analog_inputs as ai
    asked = []

    def fake(stat, item, cycle, start, end, n):
        asked.append((stat, item, cycle, end))
        return [("2026-09-01", 1.0)] if stat != "901Y105" else []
    with pytest.raises(RuntimeError):
        ai.collect_analog_inputs("2026-10-30", fetch_series=fake)
    got = ai.collect_analog_inputs("2026-10-30", fetch_series=lambda *a, **k: [("2026-09-01", 1.0)])
    assert set(got["series"]) == set(ai.ANALOG_SERIES) and all(c == "M" and e == "202610" for _, _, c, e in asked)
    p = ai.save_analog_inputs(got, dir=str(tmp_path))
    assert ai.load_analog_inputs_latest(dir=str(tmp_path)) == got and p.endswith("analog-inputs-2026-10-30.json")
    assert not ai.is_refresh_due("2026-11-05", dir=str(tmp_path)) and ai.is_refresh_due("2026-11-06", dir=str(tmp_path))
