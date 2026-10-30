"""닮은국면사례집(AnalogCasebook) — PREREG v1 규칙 고정. DIKW 검토 2026-10-30 P3."""
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


def test_analog_months_count_unjudgeable_axes_as_mismatch_and_exclude_current():
    b = lambda name: {"band": name}
    bands = {"2010-01": [b("낮음")] * 5 + [None], "2011-01": [b("낮음")] * 4 + [b("높음"), b("낮음")],
             "2012-01": [b("낮음")] * 6}
    assert ac.find_analog_months(bands, "2012-01", 5) == ["2010-01", "2011-01"]
    assert ac.find_analog_months(bands, "2012-01", 6) == []


def test_nonoverlapping_cases_keep_36_months_between_representatives():
    months = ["2010-01", "2010-02", "2012-12", "2013-01", "2013-02", "2016-02"]
    cases = ac.group_nonoverlapping_cases(months)
    assert [c["rep"] for c in cases] == ["2010-01", "2013-01", "2016-02"]
    assert cases[0]["members"] == ["2010-01", "2010-02", "2012-12"]


def test_outcome_is_from_last_known_price_month_and_pending_when_missing():
    price = {m: 100.0 + i for i, m in enumerate(_months("2020-01", 20))}
    assert ac.outcome_pct(price, "2020-02", 12) == pytest.approx((112 / 100 - 1) * 100)
    assert ac.outcome_pct(price, "2020-10", 12) is None
    assert [ac.direction(v) for v in (0.6, 0.5, -0.5, -0.6)] == ["상승", "보합", "보합", "하락"]


def test_sentence_is_withheld_below_five_cases_and_never_claims_independence():
    book = ac.build_analog_casebook(_raw(), "2026-10-30")
    s = book.stats[ac.K_MAIN][12]
    sentence = ac.casebook_sentence(book)
    if s["n"] < ac.MIN_CASES:
        assert sentence is None
    else:
        assert f"{s['n']}건" in sentence and "독립이 아닙니다" in sentence
    forced = ac.AnalogCasebook(**{**book.__dict__, "stats": {**book.stats, ac.K_MAIN: {
        12: {"n": 4, "up": 4, "down": 0, "flat": 0, "median": 3.0, "q1": 1.0, "q3": 4.0}, 36: book.stats[ac.K_MAIN][36]}}})
    assert ac.casebook_sentence(forced) is None


def test_page_lists_every_case_with_base_rate_sensitivity_and_disclaimer():
    book = ac.build_analog_casebook(_raw(), "2026-10-30")
    page = ac.render_analogs_page(book, "2026-10-30")
    text = re.sub(r"<style.*?</style>|<script.*?</script>|<[^>]+>", " ", page, flags=re.S)
    for word in ("확률", "전망", "예상", "추천", "급등", "급락"):
        assert word not in text, word
    for need in (ac.DISCLAIMER, "같은 기간 모든 달", "4개 이상 같음", "6개 이상 같음", "작성자 연쇄", "PREREG v1", ac.PREREG_PATH):
        assert need in text, need
    assert text.count("묶인 달") == len(book.cases[ac.K_MAIN])


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
