"""거시 맥락(blog/macro_context.py) — 카드·발표 직후 블록·스트립·페이지·문구 가드·다이제스트 배선. 픽스처 스냅샷, 네트워크 없음."""
import importlib.util
import json
import os
import pathlib
import re
from datetime import date

from blog import macro_context as mc
from blog.daily_digest import build_daily_digest
from blog.wording_guard import assert_lead_wording_ok

FX = os.path.join(os.path.dirname(__file__), "fixtures", "macro", "snapshot_2026-09-07.json")


def _snap():
    with open(FX, encoding="utf-8") as f:
        return json.load(f)


def _text(h):
    return re.sub(r"<[^>]+>", " ", h)


def _sample_ds():
    spec = importlib.util.spec_from_file_location("tdd_for_macro", pathlib.Path(__file__).with_name("test_daily_digest.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._sample_ds()


def test_cards_cover_strip_codes_and_are_factual():
    ctx = mc.build_macro_context(_snap(), "2026-09-07")
    by = {c["code"]: c for c in ctx["cards"]}
    assert all(c in by for c in ("bok_base", "cofix_new", "kr_govt3y", "fed_target_hi", "us10y", "usdkrw", "gold_krw_g"))
    assert by["bok_base"]["value_txt"] == "3.00%" and by["bok_base"]["delta_prev_txt"] == "+0.25%p(직전 2.75%)"
    assert by["bok_base"]["delta_12m_txt"].startswith("+0.50%p") and "2026-10-22" in by["bok_base"]["next_release_txt"]
    assert by["fed_target_hi"]["label"] == "미 연방기금 목표범위" and by["fed_target_hi"]["value_txt"] == "3.50~3.75%"
    assert "2026-09-17" in by["fed_target_hi"]["next_release_txt"] and by["fed_target_hi"]["since_txt"] == "2025-12-11 이후 같은 값"
    assert by["cofix_new"]["value_txt"] == "3.18%" and "2026-09-15" in by["cofix_new"]["next_release_txt"]
    assert by["usdkrw"]["delta_12m_txt"] == "—" and by["us10y"]["delta_12m_txt"] != "—"     # 네이버 경유는 누적 전 → '—'
    assert by["us_m2"]["value_txt"].endswith("조달러") and by["gold_krw_g"]["value_txt"].endswith("원/g")


def test_stale_and_missing_are_omitted():
    assert mc.build_macro_context(None, "2026-09-07") is None
    assert mc.build_macro_context({"asof": None}, "2026-09-07") is None
    assert mc.build_macro_context(_snap(), "2026-09-20") is None                  # 스냅샷 3일 초과 → 스트립 생략
    s = _snap()
    s["indicators"]["us10y"]["date"] = "2026-08-01"                               # 일별 지표 7일 초과 → 그 카드만 없음
    codes = [c["code"] for c in mc.build_macro_context(s, "2026-09-07")["cards"]]
    assert "us10y" not in codes and "bok_base" in codes
    s["indicators"] = {}
    assert mc.build_macro_context(s, "2026-09-07") is None                        # 카드 0개 → None


def test_release_block_after_bok_and_cofix():
    s = _snap(); s["asof"] = "2026-08-28"
    ctx = mc.build_macro_context(s, "2026-08-28")                                  # 08-27 금통위 다음 날
    txt = " ".join(ctx["release_block"]["lines"])
    assert "기준금리 3.00%" in txt and "+0.25%p" in txt and "2026-09-15" in txt
    assert "25bp 변화" in txt and "산수" in txt
    s["asof"] = "2026-08-18"
    txt = " ".join(mc.build_macro_context(s, "2026-08-18")["release_block"]["lines"])   # 08-18(화) 코픽스 공시 당일(15 토·17 대체공휴일)
    assert txt.startswith("은행연합회 코픽스 공시(2026-08-18)") and "3.18%" in txt and "반영 2026-08-18" in txt
    assert mc.build_macro_context(_snap(), "2026-09-07")["release_block"] is None
    s["indicators"].pop("bok_base"); s["asof"] = "2026-08-28"
    assert "지표 미수집" in mc.build_macro_context(s, "2026-08-28")["release_block"]["lines"][0]


def test_wording_guards_and_tistory_budget():
    ctx = mc.build_macro_context(_snap(), "2026-09-07")
    for k in ("tistory_html", "site_html", "page_html"):
        assert_lead_wording_ok(_text(ctx[k]), k)
    assert len(ctx["tistory_html"].encode()) < 3000 and "usdkrw" not in ctx["tistory_html"]
    assert set(re.findall(r"<(\w+)", ctx["tistory_html"])) <= {"p", "b", "br", "a", "span", "table", "tr", "td"}
    assert "macro_click" in ctx["site_html"] and "macro_click" not in ctx["tistory_html"]
    assert "None" not in _text(ctx["tistory_html"]) and "None" not in _text(ctx["page_html"])


def test_page_renders_calendar_lags_and_empty_state():
    page = mc.build_macro_context(_snap(), "2026-09-07")["page_html"]
    assert "2026-10-22" in page and "2026-09-17" in page and "제3조" in page and "지표 카드" in page
    assert "1~3개월" not in page and "수일~2주" not in page                 # 근거 없는 시차는 싣지 않는다(S2 Codex)
    empty = mc.render_macro_page(None, "2026-09-07")
    assert "미수집" in empty and "<h2>지표 카드</h2>" not in empty and "전달시차" in empty


def test_value_on_step_vs_observation_and_streak():
    s = [("2025-05-29", 2.5), ("2026-07-16", 2.75), ("2026-08-27", 3.0)]
    assert mc.value_on(s, "changes", date(2025, 9, 7)) == 2.5
    assert mc.value_on(s, "observations", date(2025, 9, 7)) is None
    assert mc.value_on(s, "observations", date(2026, 8, 30)) == 3.0
    assert mc.value_on(s, "changes", date(2025, 1, 1)) is None
    ind = {"freq": "monthly", "series": [["2026-01-01", 2.9], ["2026-02-01", 2.9], ["2026-03-01", 3.0], ["2026-04-01", 3.05], ["2026-05-01", 3.18]]}
    assert mc._streak_txt(ind) == "3회 연속 상승(월별 관측)"           # 인접 차이 +,+,+ 뒤 보합에서 끊김
    ind["series"] = ind["series"][:3]
    assert mc._streak_txt(ind) == ""
    ind["series"] = [["2026-01-01", 100], ["2026-02-01", 101], ["2026-03-01", 102], ["2026-04-01", 103], ["2026-05-01", 103], ["2026-06-01", 103]]
    assert mc._streak_txt(ind) == ""                                       # 보합 꼬리는 연속 아님(S2 Codex)
    ind["series"] = [["2026-01-01", 100], ["2026-03-01", 101], ["2026-04-01", 102], ["2026-05-01", 103]]
    assert mc._streak_txt(ind) == "2회 연속 상승(월별 관측)"           # 결측월(02)에서 끊겨 2회


def _ind(code, unit, freq, series, kind="observations", **kw):
    d = {"code": code, "label": code, "unit": unit, "freq": freq, "source": "s", "url": "u", "series_kind": kind,
         "value": series[-1][1], "date": series[-1][0], "since": series[-1][0], "prev_value": None, "prev_date": None,
         "since_window_start": len(series) == 1, "series": series}
    d.update(kw)
    return d


def test_release_block_handles_missing_prev_stale_pair_and_window_start():
    # 한은 관측 1점(prev_value None·since_window_start) — 08-27 결정 직후(S2 Codex 재현)
    snap = {"asof": "2026-08-28", "indicators": {"bok_base": _ind("bok_base", "%", "event", [["2026-08-27", 3.0]], kind="changes")}}
    lines = mc.build_release_block(snap, date(2026, 8, 28))["lines"]
    assert "직전값 미확인" in lines[0] and "None" not in lines[0] and "약정 재산정 주기" in lines[0]
    # FOMC 한국 발표일 09-17 — 하단은 기준일 불일치(오래된 하단) → 범위 대신 상단만, 시작일 미확인 → '같은 값 시작' 생략
    snap = {"asof": "2026-09-18", "indicators": {
        "fed_target_hi": _ind("fed_target_hi", "%", "daily", [["2026-09-17", 3.75]]),
        "fed_target_lo": _ind("fed_target_lo", "%", "daily", [["2026-09-01", 4.25]]),
        "us10y": _ind("us10y", "%", "daily", [["2026-08-01", 4.5]])}}
    lines = mc.build_release_block(snap, date(2026, 9, 18))["lines"]
    assert "상단 3.75%" in lines[0] and "4.25" not in lines[0] and "같은 값 시작" not in lines[0] and "미 국채" not in lines[0]
    # 카드도 같은 규칙: 하단 기준일 불일치면 범위 라벨을 만들지 않는다
    cards = mc.build_indicator_cards(snap, date(2026, 9, 18))
    assert cards[0].label != "미 연방기금 목표범위" and cards[0].value_txt == "3.75%"


def test_delta_12m_uses_observation_date_for_delayed_monthly():
    s = [["2025-07-01", 21000.0], ["2025-09-01", 21500.0], ["2026-06-01", 21900.0], ["2026-07-01", 22000.0]]
    c = mc._card(_ind("us_m2", "십억달러", "monthly", s, prev_value=21900.0), {}, date(2026, 9, 7))
    assert "1년 전 21.00조달러" in c.delta_12m_txt                      # 기준일 07-01 의 1년 전(오늘 기준이면 09-01 값 21.5)


def test_digest_wiring_inserts_strip_only_with_context():
    ds = _sample_ds()
    ctx = mc.build_macro_context(_snap(), "2026-09-07")
    with_ = build_daily_digest(ds, "2026-09-07", "2026-09-06", macro=ctx)
    without = build_daily_digest(ds, "2026-09-07", "2026-09-06", macro=None)
    assert "거시 지표" in with_["tistory_html"] and "거시 지표" in with_["site_html"] and 'href="../macro.html"' in with_["site_html"]
    assert "거시 지표" not in without["tistory_html"]
    assert with_["tistory_html"].index("거시 지표") < with_["tistory_html"].index("오늘의 숫자")


def test_since_window_start_suppresses_since_text():
    s = _snap()
    s["indicators"]["kr_govt3y"]["since_window_start"] = True
    by = {c["code"]: c for c in mc.build_macro_context(s, "2026-09-07")["cards"]}
    assert by["kr_govt3y"]["since_txt"] == "" and by["fed_target_hi"]["since_txt"] == "2025-12-11 이후 같은 값"


def test_digest_drops_strip_before_failing_budget(monkeypatch):
    import blog.daily_digest as dd
    ds = _sample_ds()
    ctx = mc.build_macro_context(_snap(), "2026-09-07")
    ctx["tistory_html"] = ctx["tistory_html"] + "<p>" + ("x" * 29_000) + "</p>"     # 스트립이 예산을 넘기는 상황
    d = dd.build_daily_digest(ds, "2026-09-07", "2026-09-06", macro=ctx)
    assert "거시 지표" not in d["tistory_html"] and "거시 지표" in d["site_html"] and len(d["tistory_html"].encode()) <= 30000


def test_release_block_does_not_present_previous_value_as_todays_release():
    snap = {"asof": "2026-09-15", "indicators": {"cofix_new": _ind("cofix_new", "%", "monthly", [["2026-07-16", 3.05], ["2026-08-18", 3.18]])}}
    line = mc.build_release_block(snap, date(2026, 9, 15))["lines"][0]                   # 09-15 공시일, 관측은 08-18 이 마지막
    assert line.startswith("은행연합회 코픽스 공시(2026-09-15): 발표 후 관측 없음(마지막 관측 2026-08-18 3.18%)") and "신규취급액" not in line
    assert "기준 4.50% [가정]" in mc._bp25_line()


def test_cards_drop_bad_dates_and_never_print_none(tmp_path):
    """S6d Codex R2 — 기준일이 결측·None·빈값·형식 오류인 지표는 카드에서 빠지고(수집 실패와 동일), since 결측은 '이후 같은 값' 을 단정하지 않는다."""
    import dataclasses
    from datetime import date as _date
    from blog import macro_context as mcx
    snap = json.load(open("tests/fixtures/macro/snapshot_2026-09-07.json", encoding="utf-8"))
    today = _date.fromisoformat(snap["asof"])
    base = {c.code for c in mcx.build_indicator_cards(snap, today)}
    assert "bok_base" in base
    for bad in (None, "", "2026-13-01", "del"):
        s = json.loads(json.dumps(snap)); ind = s["indicators"]["bok_base"]
        if bad == "del":
            del ind["date"]
        else:
            ind["date"] = bad
        assert {c.code for c in mcx.build_indicator_cards(s, today)} == base - {"bok_base"}
    s = json.loads(json.dumps(snap))
    for ind in s["indicators"].values():
        ind["since"] = None
    cards = mcx.build_indicator_cards(s, today)
    assert {c.code for c in cards} == base and not any("None" in str(f) or "이후 같은 값" in str(f) for c in cards for f in dataclasses.astuple(c))


def test_release_block_and_context_survive_bad_dates_and_reject_non_calendar_iso():
    """S6e Codex E1·E2 — 발표 블록은 거부된 날짜를 다시 파싱하지 않고 '지표 미수집' 으로 두며(다른 카드 유지), 검증기는 달력형 'YYYY-MM-DD' 만 받는다
    (기본형·주차형·시각 포함은 발표일 전후 문자열 비교가 어긋난다)."""
    snap = json.load(open("tests/fixtures/macro/snapshot_2026-09-07.json", encoding="utf-8"))
    for code, today in (("cofix_new", "2026-09-15"), ("fed_target_hi", "2026-09-17")):
        for bad in (None, "", "2026-13-01", "20260914", "2026-W38-1", "del"):
            s = json.loads(json.dumps(snap)); s["asof"] = today; ind = s["indicators"][code]
            if bad == "del":
                del ind["date"]
            else:
                ind["date"] = bad
            ctx = mc.build_macro_context(s, today)
            assert ctx and code not in {c["code"] for c in ctx["cards"]} and "None" not in ctx["site_html"]
            blk = mc.build_release_block(s, date.fromisoformat(today))
            assert blk and any("지표 미수집" in line and "오래됨" not in line for line in blk["lines"])
    assert mc.validate_iso_date("2026-09-14") == "2026-09-14"
    assert all(mc.validate_iso_date(v) is None for v in ("20260914", "2026-W38-1", "2026-09-07T00:00", "2026-9-4", "2026-13-01", None, "", 20260914))
