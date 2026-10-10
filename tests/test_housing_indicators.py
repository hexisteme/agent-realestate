"""Reader indicators exercise real snapshot and rendering boundaries without network."""
from copy import deepcopy

import pytest

from agent_realestate.collectors import macro as collector
from blog import macro_context as mc


def snapshot():
    codes = ["kr_mortgage_new", "seoul_apt_sale", "seoul_apt_jeonse", "seoul_unsold", "seoul_apt_sales"]
    units = ["%", "2026.06=100", "2026.06=100", "호", "호"]
    sources = ["한국은행 ECOS 121Y006/BECBLA0302", "한국은행 ECOS 901Y149/01/a7", "한국은행 ECOS 901Y150/01/a7",
               "한국은행 ECOS 901Y074/I410B", "한국부동산원 R-ONE A_2024_00554/서울/동(호)수"]
    indicators = {
        code: collector.build_indicator(code, code, unit, "monthly", source, "https://ecos.bok.or.kr/",
                                         [("2025-08-01", 10), ("2026-07-01", 11), ("2026-08-01", value)])
        for code, unit, source, value in zip(codes, units, sources, [4, 102, 101, 0, 12345])
    }
    return {"asof": "2026-10-03", "indicators": indicators, "errors": {}}


def test_five_housing_observations_have_scope_date_unit_and_reader_definition():
    ctx = mc.build_macro_context(snapshot(), "2026-10-03")
    assert ctx is not None
    for surface in ("tistory_html", "site_html", "page_html"):
        body = ctx[surface]
        for label in ("주택담보대출 금리", "서울 아파트 매매가격지수", "서울 아파트 전세가격지수", "서울 미분양 주택", "서울 아파트 매매거래량"):
            assert label in body
        assert "2026-08" in body and "2026.06=100" in body
        assert "0호" in body and "12,345호" in body
        assert "한국은행" in body and "한국부동산원" in body and "국토교통부" in body


def test_missing_or_stale_housing_values_remain_visible_without_carried_numbers():
    snap = snapshot()
    snap["carried"] = deepcopy(snap["indicators"])
    snap["indicators"]["seoul_unsold"]["date"] = "2026-01-01"
    snap["indicators"].pop("seoul_apt_sale")
    ctx = mc.build_macro_context(snap, "2026-10-03")
    for surface in ("tistory_html", "site_html"):
        assert "자료 미수집" in ctx[surface] and "기준월 오래됨" in ctx[surface]
        assert "서울 아파트 매매가격지수" in ctx[surface]


def test_ecos_series_rejects_wrong_scope_duplicate_month_nonfinite_and_future(monkeypatch):
    import json
    monkeypatch.setattr(collector, "_load_env_file", lambda: None)
    monkeypatch.setenv("ECOS_API_KEY", "synthetic-key")
    row = {"STAT_CODE": "901Y149", "ITEM_CODE1": "01", "ITEM_CODE2": "a7",
           "UNIT_NAME": "2026.06=100", "TIME": "202608", "DATA_VALUE": "102"}
    for mutate in (lambda r: r.update(ITEM_CODE2="a8"), lambda r: r.update(DATA_VALUE="NaN"),
                   lambda r: r.update(TIME="202612"), lambda r: r.update(UNIT_NAME="2021.06=100")):
        bad = deepcopy(row)
        mutate(bad)
        fetch = lambda *args: json.dumps({"StatisticSearch": {"row": [bad]}})
        with pytest.raises(RuntimeError):
            collector.fetch_ecos_series("901Y149", "01/a7", "M", "202501", "202610", fetch=fetch,
                                        expected_unit="2026.06=100")
    fetch = lambda *args: json.dumps({"StatisticSearch": {"row": [row, row]}})
    with pytest.raises(RuntimeError):
        collector.fetch_ecos_series("901Y149", "01/a7", "M", "202501", "202610", fetch=fetch,
                                    expected_unit="2026.06=100")


def rone_response(rows, total=None):
    return {"SttsApiTblData": [{"head": [{"list_total_count": len(rows) if total is None else total},
                                        {"RESULT": {"CODE": "INFO-000"}}]}, {"row": rows}]}


def rone_row():
    return {"STATBL_ID": "A_2024_00554", "CLS_ID": 500002, "ITM_ID": 100001,
            "UI_NM": "호수", "DTACYCLE_CD": "MM", "WRTTIME_IDTFR_ID": "202608", "DTA_VAL": 12345}


def test_public_rone_window_is_complete_and_rejects_wrong_scope_or_sample_truncation():
    import json
    fetch = lambda *args: json.dumps(rone_response([rone_row()]))
    assert collector.fetch_seoul_apt_sales("2026-10-03", fetch=fetch) == [("2026-08-01", 12345)]
    for field, value in (("CLS_ID", 500003), ("ITM_ID", 100002), ("UI_NM", "제곱미터"),
                         ("DTA_VAL", -1), ("DTA_VAL", "NaN"), ("WRTTIME_IDTFR_ID", "202612")):
        row = rone_row() | {field: value}
        fetch = lambda *args: json.dumps(rone_response([row]))
        with pytest.raises(RuntimeError):
            collector.fetch_seoul_apt_sales("2026-10-03", fetch=fetch)
    fetch = lambda *args: json.dumps(rone_response([rone_row()], total=6))
    with pytest.raises(RuntimeError):
        collector.fetch_seoul_apt_sales("2026-10-03", fetch=fetch)


def test_unreachable_legacy_market_url_uses_verified_official_ecos_source(monkeypatch):
    import json
    monkeypatch.setattr(collector, "_load_env_file", lambda: None)
    monkeypatch.setenv("ECOS_API_KEY", "synthetic-key")
    def fetch(url, encoding="utf-8"):
        if "/817Y002/D/" in url:
            item = "010200000" if "010200000" in url else "010502000"
            return json.dumps({"StatisticSearch": {"row": [
                {"STAT_CODE": "817Y002", "ITEM_CODE1": item, "UNIT_NAME": "연%",
                 "TIME": "20261002", "DATA_VALUE": "3.12"}]}})
        if "/731Y001/D/" in url:
            return json.dumps({"StatisticSearch": {"row": [
                {"STAT_CODE": "731Y001", "ITEM_CODE1": "0000001", "UNIT_NAME": "원",
                 "TIME": "20261002", "DATA_VALUE": "1300"}]}})
        raise OSError("offline fixture")
    prev = {"indicators": {"kr_govt3y": {"source": "네이버 경유", "unit": "%", "freq": "daily",
                                        "series": [["2025-01-01", 99]]}}}
    out = collector.collect_macro("2026-10-03", prev=prev, fetch=fetch, naver_pages=1)
    for code in ("kr_govt3y", "cd91", "usdkrw"):
        assert code in out["indicators"] and code not in out["errors"]
        assert out["indicators"][code]["url"] == "https://ecos.bok.or.kr/"
        assert out["indicators"][code]["date"] == "2026-10-02"
    assert out["indicators"]["kr_govt3y"]["series"] == [["2026-10-02", 3.12]]


def test_reader_table_keeps_actual_source_label_and_wraps_long_observations():
    from blog.market_indicators import render_reader_table
    ctx = {"cards": [{"code": "kr_govt3y", "source": "금융투자협회 고시(네이버금융 경유)",
                      "url": "https://finance.naver.com/marketindex/", "value_txt": "3.12%",
                      "date": "2026-10-02", "delta_prev_txt": "+0.01%p"}]}
    body = render_reader_table(ctx, ("kr_govt3y",), site=False)
    assert "금융투자협회" in body and "네이버금융" in body and "한국은행 ECOS" not in body
