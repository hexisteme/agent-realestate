"""Public bank gold quotes retain observed units, dates, and explicit failures."""
from datetime import date, timedelta
import importlib
import importlib.util
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from agent_realestate.collectors import macro

FIXTURE = Path(__file__).parent / "fixtures/macro/woori_gold.html"


def _module():
    name = "agent_realestate.collectors.macro_gold"
    assert importlib.util.find_spec(name) is not None, "official public gold source API is missing"
    return importlib.import_module(name)


def _table(observed="2026.10.02", krw="190,123.45", usd="4,123.56"):
    return ('<table><caption>금가격 조회 결과 정보</caption><tr><th>일자</th>'
            '<th>기준가격(원/g)</th><th>금가격 (USD/트로이온즈)</th></tr>'
            f'<tr><td>{observed}</td><td>{krw}</td><td>{usd}</td></tr></table>')


def test_official_public_fixture_has_two_observed_daily_series():
    gold = _module().parse_woori_gold(FIXTURE.read_text())
    assert set(gold) == {"gold_krw_g", "gold_usd_oz"}
    assert len(gold["gold_krw_g"]) == len(gold["gold_usd_oz"]) == 22
    assert gold["gold_krw_g"][0][0] == "2026-09-01"
    assert gold["gold_krw_g"][-1][0] == gold["gold_usd_oz"][-1][0] == "2026-10-02"
    # Values bind to distinct reported columns, not buy/sell prices or computed FX conversions.
    single = _module().parse_woori_gold(_table())
    assert single == {"gold_krw_g": [("2026-10-02", 190123.45)],
                      "gold_usd_oz": [("2026-10-02", 4123.56)]}


def test_nested_text_and_reordered_unit_bound_columns():
    body = ('<table><caption>금가격 조회 결과 정보</caption><tr>'
            '<th>금가격<br>(USD/트로이온즈)</th><th>일자</th><th>기준가격(원/g)</th></tr>'
            '<tr><td><b>4,123.56</b></td><td>2026.10.02</td><td><span>190,123.45</span></td></tr></table>')
    assert _module().parse_woori_gold(body)["gold_usd_oz"] == [("2026-10-02", 4123.56)]


@pytest.mark.parametrize("body", [
    _table().replace("원/g", "원/100g"),
    _table().replace("USD/트로이온즈", "USD/g"),
    _table().replace("기준가격", "매입가격"),
    _table().replace("2026.10.02", "2026.02.30"),
    _table(krw="NaN"), _table(krw="inf"), _table(krw="-10"), _table(krw="0"),
    _table(usd="—"), _table(usd="1e309"), _table(usd="True"),
    _table().replace('<td>4,123.56</td>', ''),
    _table().replace('<td>190,123.45</td>', '<td colspan="2">190,123.45</td>'),
    _table() + _table(krw="190,000"),
    _table().replace('<tr><td>2026.10.02</td><td>190,123.45</td><td>4,123.56</td></tr>', ''),
], ids=["wrong-krw-unit", "wrong-usd-unit", "buy-price-not-basis", "invalid-date", "nan", "infinity",
        "negative", "zero", "missing-usd", "nonfinite-exponent", "boolean", "short-row", "spanned-row",
        "ambiguous-table", "empty-table"])
def test_corrupt_missing_or_wrong_definition_table_is_unavailable(body):
    with pytest.raises(ValueError, match="공식 금"):
        _module().parse_woori_gold(body)


def test_identical_rows_dedupe_and_conflicting_same_date_fails():
    original = '<tr><td>2026.10.02</td><td>190,123.45</td><td>4,123.56</td></tr>'
    assert len(_module().parse_woori_gold(_table().replace(original, original * 2))["gold_krw_g"]) == 1
    with pytest.raises(ValueError, match="공식 금"):
        _module().parse_woori_gold(_table().replace(original, original + original.replace("190,123.45", "190,999.00")))


def test_fetch_is_public_date_bounded_and_keeps_friday_date_on_sunday():
    calls = []
    def fetch(url, encoding):
        calls.append((url, encoding))
        return FIXTURE.read_text()
    result = _module().fetch_woori_gold("2026-10-04", fetch)
    assert result["gold_krw_g"][-1][0] == "2026-10-02"
    assert len(calls) == 1 and calls[0][1] == "utf-8"
    parsed = urlsplit(calls[0][0])
    assert parsed.scheme == "https" and parsed.hostname == "spot.wooribank.com" and parsed.path == "/pot/jcc"
    assert parse_qs(parsed.query) == {"withyou": ["POGLD0006"], "__ID": ["c007327"],
                                     "BAS_SDT": [(date(2026, 10, 4)-timedelta(days=400)).strftime("%Y%m%d")],
                                     "BAS_EDT": ["20261004"], "term": ["day"]}


@pytest.mark.parametrize("observed", ["2026.10.05", "2026.09.20", "2024.01.02"])
def test_future_stale_or_query_window_escape_observation_is_unavailable(observed):
    with pytest.raises(ValueError, match="공식 금"):
        _module().fetch_woori_gold("2026-10-04", lambda *_: _table(observed))


def test_upstream_failure_never_exposes_request_or_secret_details():
    def fetch(*_):
        raise OSError("secret-fixture-private-query")
    with pytest.raises(ValueError) as failure:
        _module().fetch_woori_gold("2026-10-04", fetch)
    assert "secret-fixture-private-query" not in str(failure.value)


def _gold_only_fetch(url, encoding="utf-8"):
    if "wooribank.com/pot/jcc" in url:
        return FIXTURE.read_text()
    raise OSError("unrelated-source-fixture-offline")


def test_collector_recovers_both_codes_without_retrying_retired_endpoint(monkeypatch):
    monkeypatch.setattr(macro, "_load_env_file", lambda: None)
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    calls = []
    def fetch(url, encoding="utf-8"):
        calls.append(url)
        return _gold_only_fetch(url, encoding)
    result = macro.collect_macro("2026-10-04", fetch=fetch, naver_pages=1)
    assert {"gold_krw_g", "gold_usd_oz"} <= result["indicators"].keys()
    assert not ({"gold_krw_g", "gold_usd_oz"} & result["errors"].keys())
    assert not any("goldDailyQuote" in url for url in calls)
    for code, unit in (("gold_krw_g", "원/g"), ("gold_usd_oz", "$/oz")):
        item = result["indicators"][code]
        assert item["unit"] == unit and item["freq"] == "daily" and item["date"] == "2026-10-02"
        assert "우리은행" in item["label"] and "우리은행" in item["source"]
        assert item["url"] == "https://spot.wooribank.com/pot/Dream?withyou=POGLD0006"


def test_new_bank_definition_does_not_mix_previous_naver_values(monkeypatch):
    monkeypatch.setattr(macro, "_load_env_file", lambda: None)
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    old = {"gold_krw_g": {"source": "네이버금융 금 시세(은행 고시)", "unit": "원/g", "freq": "daily",
                          "series": [["2026-08-20", 999999.0]]}}
    result = macro.collect_macro("2026-10-04", prev={"carried": old}, fetch=_gold_only_fetch, naver_pages=1)
    assert "gold_krw_g" in result["indicators"]
    assert result["indicators"]["gold_krw_g"]["series"][0][0] == "2026-09-01"
    assert all(value != 999999.0 for _, value in result["indicators"]["gold_krw_g"]["series"])


def test_unavailable_bank_keeps_errors_and_history_without_success(monkeypatch):
    monkeypatch.setattr(macro, "_load_env_file", lambda: None)
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    previous = {code: {"source": "old-source", "series": [["2026-09-16", 100.0]]}
                for code in ("gold_krw_g", "gold_usd_oz")}
    result = macro.collect_macro("2026-10-04", prev={"carried": previous},
                                 fetch=lambda *_: _table(usd="—"), naver_pages=1)
    assert not ({"gold_krw_g", "gold_usd_oz"} & result["indicators"].keys())
    assert {"gold_krw_g", "gold_usd_oz"} <= result["errors"].keys()
    assert result["carried"]["gold_krw_g"] == previous["gold_krw_g"]
    assert result["n_ok"] == len(result["indicators"])
