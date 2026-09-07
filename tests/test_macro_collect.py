"""거시 지표 수집기(agent_realestate/collectors/macro.py) + macro 스냅샷 — 픽스처 기반, 네트워크 없음."""
import json
import os
from datetime import date

import pytest

from agent_realestate.collectors import macro as m
from blog.snapshots import save_macro_snapshot, load_macro_snapshot_latest, macro_snapshot_path_latest

FX = os.path.join(os.path.dirname(__file__), "fixtures", "macro")


def _read(name):
    with open(os.path.join(FX, name), encoding="utf-8") as f:
        return f.read()


def _fixture_fetch(url, encoding="utf-8"):
    if "ecos.bok.or.kr" in url:                                   # bok.or.kr 분기보다 먼저(부분 문자열)
        return _read("ecos_m2.json") if "161Y005" in url else _read("ecos_base_rate.json")
    if "bok.or.kr" in url:
        return _read("bok_base_rate.html")
    if "fredgraph.csv?id=" in url:
        fid = url.rsplit("=", 1)[1]
        return _read(f"fred_{fid}.csv") if fid in ("DGS10", "DFEDTARU", "M2SL") else _read("fred_generic.csv")
    if "interestDailyQuote" in url:
        return _read("naver_cofix.html") if "COFIX" in url else _read("naver_govt3y.html")
    if "goldDailyQuote" in url:
        return _read("naver_gold.html")
    if "exchangeDailyQuote" in url:
        return _read("naver_fx.html")
    raise AssertionError(url)


@pytest.fixture(autouse=True)
def _no_delay(monkeypatch):
    monkeypatch.setattr(m, "PAGE_DELAY_S", 0)


@pytest.fixture(autouse=True)
def _no_ecos_key(monkeypatch):
    """개발 머신의 .env 에 든 ECOS 키가 픽스처 테스트를 라이브 경로로 보내지 않게 — 키는 테스트가 명시로 넣는다."""
    monkeypatch.setattr(m, "_load_env_file", lambda: None)
    monkeypatch.delenv("ECOS_API_KEY", raising=False)


def test_parse_bok_base_rate_is_ascending_change_points():
    s = m.parse_bok_base_rate(_read("bok_base_rate.html"))
    assert s[-1] == ("2026-08-27", 3.0) and s[-2] == ("2026-07-16", 2.75)
    assert s == sorted(s) and len(s) >= 60
    with pytest.raises(ValueError):
        m.parse_bok_base_rate("<html>표 없음</html>")


def test_parse_fred_csv_skips_missing_dots():
    s = m.parse_fred_csv(_read("fred_generic.csv"))
    assert s == [("2026-08-31", 4.10), ("2026-09-02", 4.20)]
    assert m.parse_fred_csv(_read("fred_DGS10.csv"))[-1][0] >= "2026-09-01"
    with pytest.raises(ValueError):
        m.parse_fred_csv("observation_date,X\n")


def test_parse_naver_interest_rows():
    s = m.parse_naver_interest(_read("naver_cofix.html"))
    assert s == sorted(s) and all(len(d) == 10 and d[4] == "-" for d, _ in s)
    assert ("2026-08-18", 3.18) in s and ("2026-08-14", 3.05) in s
    g = m.parse_naver_interest(_read("naver_govt3y.html"))
    assert 5 <= len(g) <= 12 and all(2.0 < v < 8.0 for _, v in g)


def test_parse_naver_gold_and_fx_columns():
    g = m.parse_naver_gold(_read("naver_gold.html"))
    assert set(g) == {"gold_krw_g", "gold_usd_oz", "usdkrw_ref"} and len(g["gold_krw_g"]) >= 5
    d, krw = g["gold_krw_g"][-1]
    assert d.startswith("2026-") and krw > 100_000
    assert 2_000 < g["gold_usd_oz"][-1][1] < 10_000 and 1_000 < g["usdkrw_ref"][-1][1] < 2_000
    fx = m.parse_naver_fx(_read("naver_fx.html"))
    assert len(fx) >= 5 and 1_000 < fx[-1][1] < 2_000 and fx == sorted(fx)


def test_parse_ecos_json_and_error():
    assert m.parse_ecos_json(_read("ecos_base_rate.json")) == [("2026-07-16", 2.75), ("2026-08-27", 3.0)]
    assert m.parse_ecos_json(_read("ecos_m2.json")) == [("2026-04-01", 4152205.0), ("2026-05-01", 4183579.2), ("2026-06-01", 4212955.4)]   # 월별 TIME → 월초
    with pytest.raises(ValueError, match="INFO-100"):
        m.parse_ecos_json(_read("ecos_error.json"))
    leak = '{"RESULT":{"CODE":"dummy-key-XYZ","MESSAGE":"dummy-key-XYZ"}}'                     # 응답 원문이 키를 되풀이해도 예외엔 없다(S9 Codex P1)
    with pytest.raises(m.EcosResponseError) as ei:
        m.parse_ecos_json(leak)
    assert str(ei.value) == "ECOS 응답 오류 코드 ?"
    bad = '{"StatisticSearch":{"row":[{"TIME":"202606","DATA_VALUE":"dummy-key-XYZ"}]}}'
    with pytest.raises(m.EcosResponseError) as ei:
        m.parse_ecos_json(bad)
    assert str(ei.value) == "ECOS DATA_VALUE 가 숫자가 아님"


def test_merge_series_dedupes_orders_and_caps():
    old = [("2026-01-02", 1.0), ("2026-01-01", 0.5)]
    new = [("2026-01-02", 1.5), ("2026-01-03", 2.0)]
    assert m.merge_series(old, new) == [("2026-01-01", 0.5), ("2026-01-02", 1.5), ("2026-01-03", 2.0)]
    assert len(m.merge_series([(f"2025-01-{i:02d}", i) for i in range(1, 29)], [], keep=5)) == 5


def test_collapse_changes_and_build_indicator_since_prev():
    obs = [("d1", 2.5), ("d2", 2.75), ("d3", 3.0), ("d4", 3.0)]
    assert m.collapse_changes(obs) == [("d1", 2.5), ("d2", 2.75), ("d3", 3.0)]
    ind = m.build_indicator("x", "라벨", "%", "daily", "출처", "url", obs)
    assert (ind["value"], ind["date"], ind["since"], ind["prev_value"], ind["prev_date"]) == (3.0, "d4", "d3", 2.75, "d2")
    with pytest.raises(ValueError):
        m.build_indicator("x", "라벨", "%", "daily", "출처", "url", [])


def test_collect_macro_with_fixtures_collects_all_sources():
    out = m.collect_macro("2026-09-07", fetch=_fixture_fetch)
    assert out["errors"] == {} and out["n_ok"] >= 12
    ind = out["indicators"]
    assert ind["bok_base"]["value"] == 3.0 and ind["bok_base"]["series_kind"] == "changes" and ind["bok_base"]["prev_value"] == 2.75
    assert ind["cofix_new"]["value"] == 3.18 and ind["cofix_new"]["since"] == "2026-08-18" and ind["cofix_new"]["prev_value"] == 3.05
    assert ind["us10y"]["unit"] == "%" and ind["gold_krw_g"]["value"] > 100_000 and ind["usdkrw"]["value"] > 1_000
    assert all(isinstance(v["series"], list) and v["series"][-1][0] == v["date"] for v in ind.values())


def test_collect_macro_omits_failed_sources_and_keeps_others():
    def flaky(url, encoding="utf-8"):
        if "naver.com" in url:
            raise OSError("연결 실패")
        return _fixture_fetch(url, encoding)
    out = m.collect_macro("2026-09-07", fetch=flaky)
    assert "cofix_new" not in out["indicators"] and "gold_krw_g" not in out["indicators"] and "usdkrw" not in out["indicators"]
    assert out["errors"]["cofix_new"].startswith("OSError") and "Traceback" not in out["errors"]["cofix_new"]
    assert "bok_base" in out["indicators"] and "us10y" in out["indicators"]


def test_collect_macro_merges_previous_snapshot_series():
    prev = {"indicators": {"kr_govt3y": {"series": [["2025-01-02", 2.6], ["2025-01-03", 2.62]]}}}
    out = m.collect_macro("2026-09-07", prev=prev, fetch=_fixture_fetch)
    s = out["indicators"]["kr_govt3y"]["series"]
    assert s[0] == ["2025-01-02", 2.6] and s[-1][0] >= "2026-08-01"


def test_ecos_requires_key_and_never_leaks_it(monkeypatch):
    monkeypatch.setattr(m, "_load_env_file", lambda: None)
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    with pytest.raises(m.MacroSourceMissing):
        m.fetch_ecos_series("722Y001", "0101000", "D", "20260101", "20261231")
    monkeypatch.setenv("ECOS_API_KEY", "dummy-key-XYZ")
    def boom(url, encoding="utf-8"):
        raise OSError(f"bad url {url}")
    with pytest.raises(RuntimeError) as ei:
        m.fetch_ecos_series("722Y001", "0101000", "D", "20260101", "20261231", fetch=boom)
    assert "dummy-key-XYZ" not in str(ei.value) and ei.value.__cause__ is None
    ok = lambda url, encoding="utf-8": _read("ecos_base_rate.json")  # noqa: E731
    assert m.fetch_ecos_series("722Y001", "0101000", "D", "20260101", "20261231", fetch=ok)[-1] == ("2026-08-27", 3.0)


def test_collect_macro_kr_m2_only_with_key_and_never_leaks(monkeypatch):
    out = m.collect_macro("2026-09-07", fetch=_fixture_fetch)                          # 키 없음 → 실패가 아니라 생략
    assert "kr_m2" not in out["indicators"] and "kr_m2" not in out["errors"]
    monkeypatch.setenv("ECOS_API_KEY", "dummy-key-XYZ")
    seen = []
    def spy(url, encoding="utf-8"):
        seen.append(url)
        return _fixture_fetch(url, encoding)
    out = m.collect_macro("2026-09-07", fetch=spy)
    k = out["indicators"]["kr_m2"]
    assert (k["value"], k["date"], k["unit"], k["freq"], k["prev_value"]) == (4212955.4, "2026-06-01", "십억원", "monthly", 4183579.2)
    assert k["source"] == "한국은행 ECOS 161Y005/BBHS00" and k["url"] == m.ECOS_PAGE and out["errors"] == {}
    assert any(u.endswith("/1000/161Y005/M/200001/202609/BBHS00") for u in seen)   # 월별 창 = 통계 시작 이전 ~ 이번 달
    assert "dummy-key-XYZ" not in json.dumps(out, ensure_ascii=False)                 # 스냅샷 어디에도 키가 없다
    def ecos_down(url, encoding="utf-8"):
        if "ecos.bok.or.kr" in url:
            raise OSError(f"bad {url}")
        return _fixture_fetch(url, encoding)
    out = m.collect_macro("2026-09-07", prev=out, fetch=ecos_down)
    assert "kr_m2" not in out["indicators"] and out["errors"]["kr_m2"] == "RuntimeError: ECOS 요청 실패(OSError)"
    assert "dummy-key-XYZ" not in json.dumps(out, ensure_ascii=False) and "bok_base" in out["indicators"]
    assert out["carried"]["kr_m2"]["value"] == 4212955.4                             # 이력은 carried 로, 카드 재료 아님
    for body, want in (('{"RESULT":{"CODE":"INFO-100","MESSAGE":"dummy-key-XYZ 인증 실패"}}', "ECOS 응답 오류 코드 INFO-100"),
                       ('{"RESULT":{"CODE":"dummy-key-XYZ"}}', "ECOS 응답 오류 코드 ?"),
                       ('<html>dummy-key-XYZ</html>', "JSONDecodeError"),
                       ('{"StatisticSearch":{"row":[{"TIME":"202606","DATA_VALUE":"dummy-key-XYZ"}]}}', "ECOS DATA_VALUE 가 숫자가 아님")):
        def ecos_body(url, encoding="utf-8", body=body):
            return body if "ecos.bok.or.kr" in url else _fixture_fetch(url, encoding)
        out = m.collect_macro("2026-09-07", fetch=ecos_body)
        assert out["errors"]["kr_m2"] == f"RuntimeError: ECOS 응답 해석 실패({want})" and "kr_m2" not in out["indicators"]
        assert "dummy-key-XYZ" not in json.dumps(out, ensure_ascii=False) and out["n_ok"] == 15   # 비정상 응답도 스냅샷에 원문이 남지 않는다


def test_calendar_next_upcoming_recent():
    t = date(2026, 9, 7)
    assert m.next_release("bok", t) == date(2026, 10, 22)
    assert m.next_release("fomc", t) == date(2026, 9, 17)             # 미국 09-16 회의 → 한국 발표일 09-17(S2 Codex)
    assert m.next_release("cofix", t) == date(2026, 9, 15)
    assert m.cofix_release_date(2026, 11) == date(2026, 11, 16)      # 11-15 일요일 → 월요일
    assert m.cofix_release_date(2026, 8) == date(2026, 8, 18)        # 08-15 토 · 08-17 대체공휴일 → 화(실측 since 08-18)
    assert m.cofix_release_date(2026, 2) == date(2026, 2, 19)        # 02-15 일 · 16~18 설 연휴 → 목
    assert m.cofix_release_date(2027, 1) == date(2027, 1, 15)        # 미등록 연도 평일 = 그대로
    up = m.upcoming_releases(t)
    assert [u["date"] for u in up][:3] == ["2026-09-15", "2026-09-17", "2026-10-15"]
    assert m.recent_releases(date(2026, 8, 28))[0]["kind"] == "bok" and m.recent_releases(date(2026, 9, 7)) == []
    assert m.next_release("bok", date(2027, 1, 1)) is None            # 2027 미등록 → None(카드는 '미등록')


def test_macro_snapshot_save_load_prune(tmp_path):
    d = str(tmp_path)
    save_macro_snapshot({"asof": "2026-08-10", "indicators": {}}, "2026-08-10", dir=d)     # 월요일, 28일 전 → 삭제
    save_macro_snapshot({"asof": "2026-08-16", "indicators": {}}, "2026-08-16", dir=d)     # 일요일 → 보존
    p = save_macro_snapshot({"asof": "2026-09-07", "indicators": {"x": 1}}, "2026-09-07", dir=d)
    names = sorted(os.listdir(d))
    assert names == ["macro-2026-08-16.json", "macro-2026-09-07.json"] and p.endswith("macro-2026-09-07.json")
    assert load_macro_snapshot_latest(dir=d)["indicators"] == {"x": 1}
    assert macro_snapshot_path_latest(dir=d, on_or_before=date(2026, 9, 1)).endswith("macro-2026-08-16.json")
    assert load_macro_snapshot_latest(dir=str(tmp_path / "none")) is None


def test_empty_page_is_failure_and_history_is_carried():
    def empty_naver(url, encoding="utf-8"):
        if "interestDailyQuote" in url:
            return "<table><tr><th>날짜</th></tr></table>"                 # 행 없음 → 실패로 기록
        return _fixture_fetch(url, encoding)
    prev = {"indicators": {"kr_govt3y": {"series": [["2025-01-02", 2.6]], "value": 2.6, "date": "2025-01-02"}}}
    out = m.collect_macro("2026-09-07", prev=prev, fetch=empty_naver)
    assert "kr_govt3y" not in out["indicators"] and out["errors"]["kr_govt3y"].startswith("ValueError: 새 관측 없음")
    assert out["carried"]["kr_govt3y"]["series"] == [["2025-01-02", 2.6]]           # 이력은 보존, 카드 재료 아님
    again = m.collect_macro("2026-09-08", prev=out, fetch=_fixture_fetch)             # 다음 회차가 이어받아 병합
    assert again["indicators"]["kr_govt3y"]["series"][0] == ["2025-01-02", 2.6] and "kr_govt3y" not in again["carried"]


def test_bok_cells_with_whitespace_and_gold_column_guard():
    html = _read("bok_base_rate.html").replace('<td class="fb">2026</td>', '<td class="fb">\n 2026 </td>', 1)
    assert m.parse_bok_base_rate(html)[-1] == ("2026-08-27", 3.0)
    gold = _read("naver_gold.html")
    with pytest.raises(ValueError, match="헤더"):
        m.parse_naver_gold(gold.replace("기준 국제 금 시세", "국제금"))
    i = gold.index('<td class="date">'); j = gold.index("</tr>", i)
    with pytest.raises(ValueError, match="열 수"):
        m.parse_naver_gold(gold[:j] + "<td>1</td>" + gold[j:])


def test_since_window_start_flag():
    flat = [("2026-01-01", 3.0), ("2026-01-02", 3.0)]
    ind = m.build_indicator("x", "라벨", "%", "daily", "출처", "url", flat)
    assert ind["since_window_start"] is True and ind["prev_value"] is None and ind["since"] == "2026-01-01"
    assert m.build_indicator("x", "라벨", "%", "daily", "출처", "url", [("2025-12-31", 2.5)] + flat)["since_window_start"] is False
