"""MOLIT 매매 수집 경계: 비밀·네트워크·실캐시 없이 실제 함수와 XML 검증."""
from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import types
from datetime import date
from pathlib import Path
from unittest.mock import Mock
import urllib.parse
import xml.etree.ElementTree as ET

import pytest


PROJECT = Path(__file__).resolve().parents[1]
FETCH_SCRIPTS = ("fetch_molit_recent_25gu.py", "fetch_molit_recent_11gu.py")
ACTIVE_ITEM = """<item><aptNm>합성단지</aptNm><excluUseAr>59.5</excluUseAr>
<dealAmount>83,000</dealAmount><dealYear>2026</dealYear><dealMonth>8</dealMonth>
<dealDay>12</dealDay><sggCd>11110</sggCd><cdealType> </cdealType><cdealDay> </cdealDay></item>"""


def _response(items=ACTIVE_ITEM, *, code="000", total=None, page=1, size=4000):
    count = items.count("<item>") if total is None else total
    return (f"<response><header><resultCode>{code}</resultCode><resultMsg>Fixture</resultMsg></header>"
            f"<body><items>{items}</items><totalCount>{count}</totalCount>"
            f"<pageNo>{page}</pageNo><numOfRows>{size}</numOfRows></body></response>")


def _collector_functions(script, xml_or_error):
    """Compile exact production function bodies, excluding credential/DNS main setup.

    Transport input is a bytes fixture. No synthetic replacement of parsing logic
    is used; old and new function bodies both run with the same dependencies.
    """
    tree = ast.parse((PROJECT / script).read_text(encoding="utf-8"))
    functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    selected = [node for node in functions if node.name in ("_t", "_rolling_months", "fetch", "fetch_batch", "main")]
    opener = Mock()
    if isinstance(xml_or_error, Exception):
        opener.side_effect = xml_or_error
    else:
        payload = xml_or_error.encode("utf-8") if isinstance(xml_or_error, str) else xml_or_error
        opener.side_effect = lambda *_args, **_kwargs: io.BytesIO(payload)
    namespace = {"urllib": types.SimpleNamespace(parse=urllib.parse, request=types.SimpleNamespace(urlopen=opener)),
                 "time": types.SimpleNamespace(sleep=Mock()), "ET": ET, "date": date,
                 "K": "synthetic-fixture-key", "EP": "https://fixture.invalid/molit"}
    try:
        from blog.molit_transactions import MolitTradeResponseError, parse_molit_trade_response
    except ModuleNotFoundError:
        pass  # Actual legacy fetch regression does not depend on the new parser.
    else:
        namespace.update(MolitTradeResponseError=MolitTradeResponseError,
                         parse_molit_trade_response=parse_molit_trade_response)
        from blog import molit_transactions
        if hasattr(molit_transactions, "validate_molit_trade_cache"):
            namespace["validate_molit_trade_cache"] = molit_transactions.validate_molit_trade_cache
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(PROJECT / script), "exec"), namespace)
    return namespace, opener


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
@pytest.mark.parametrize("marker", ("flag", "date"))
def test_collectors_exclude_cancelled_transactions(script, marker):
    cancelled = ACTIVE_ITEM.replace("<aptNm>합성단지</aptNm>", "<aptNm>취소합성단지</aptNm>")
    if marker == "flag":
        cancelled = cancelled.replace("<cdealType> </cdealType>", "<cdealType>O</cdealType>")
    else:
        cancelled = cancelled.replace("<cdealDay> </cdealDay>", "<cdealDay>20260901</cdealDay>")
    namespace, _opener = _collector_functions(script, _response(ACTIVE_ITEM + cancelled))
    rows = namespace["fetch"]("11110", "202608")
    assert [row["apt"] for row in rows] == ["합성단지"]


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
@pytest.mark.parametrize("xml", (
    _response("", code="30"),
    _response(ACTIVE_ITEM.replace("<dealAmount>83,000</dealAmount>", "<dealAmount/>")),
    _response(ACTIVE_ITEM.replace("<excluUseAr>59.5</excluUseAr>", "<excluUseAr/>")),
    _response(ACTIVE_ITEM.replace("<dealDay>12</dealDay>", "<dealDay>32</dealDay>")),
    _response(ACTIVE_ITEM.replace("<dealMonth>8</dealMonth>", "<dealMonth>7</dealMonth>")),
    _response(ACTIVE_ITEM, total=2),
), ids=("api-error", "missing-price", "missing-area", "invalid-day", "wrong-month", "incomplete-page"))
def test_collectors_return_failure_not_success_for_invalid_responses(script, xml):
    namespace, _opener = _collector_functions(script, xml)
    assert namespace["fetch"]("11110", "202608") is None


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_collectors_accept_only_explicit_successful_zero_and_preserve_list_contract(script):
    namespace, _opener = _collector_functions(script, _response(""))
    assert namespace["fetch"]("11110", "202608") == []


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_collectors_transport_failure_is_not_successful_zero(script):
    namespace, opener = _collector_functions(script, OSError("synthetic transport failure"))
    assert namespace["fetch"]("11110", "202608") is None
    assert opener.call_count == (2 if "25gu" in script else 4)


def _parse(xml, lawd="11110", ym="202608"):
    from blog.molit_transactions import parse_molit_trade_response
    return parse_molit_trade_response(xml, lawd, ym)


def _parse_error():
    from blog.molit_transactions import MolitTradeResponseError
    return MolitTradeResponseError


def test_parser_preserves_legacy_fields_and_adds_verifiable_provenance():
    xml = _response(ACTIVE_ITEM)
    batch = _parse(xml)
    row = batch["rows"][0]
    assert {key: row[key] for key in ("apt", "area", "price", "ym")} == {
        "apt": "합성단지", "area": 59.5, "price": 830_000_000, "ym": "202608"}
    assert row["deal_date"] == "2026-08-12"
    assert row["cancellation_checked"] is True
    provenance = row["provenance"]
    assert provenance["schema"] == "MolitTradeRow/v1"
    assert provenance["source"] == "MOLIT_RTMS_APT_TRADE_DEV"
    assert provenance["lawd_cd"] == "11110" and provenance["request_ym"] == "202608"
    assert provenance["response_sha256"] == hashlib.sha256(xml.encode()).hexdigest()
    assert provenance["cdeal_type"] == provenance["cdeal_day"] == ""
    receipt = batch["receipt"]
    assert receipt["schema"] == "MolitTradeBatch/v1"
    assert receipt["total_count"] == receipt["accepted_count"] == 1
    assert receipt["cancelled_count"] == 0
    assert receipt["cancellation_filter_applied"] is True


@pytest.mark.parametrize("cancel_date", ("20260901", "2026-09-01", "26.09.01", "2026.09.01"))
def test_parser_cancel_date_alone_excludes_and_counts_row(cancel_date):
    cancelled = ACTIVE_ITEM.replace("<cdealDay> </cdealDay>", f"<cdealDay>{cancel_date}</cdealDay>")
    batch = _parse(_response(cancelled))
    assert batch["rows"] == []
    assert batch["receipt"]["total_count"] == batch["receipt"]["cancelled_count"] == 1
    assert batch["receipt"]["accepted_count"] == 0


@pytest.mark.parametrize("marker", ("O",))
def test_parser_cancel_marker_excludes_even_without_cancel_date(marker):
    cancelled = ACTIVE_ITEM.replace("<cdealType> </cdealType>", f"<cdealType>{marker}</cdealType>")
    assert _parse(_response(cancelled))["rows"] == []


@pytest.mark.parametrize("marker", ("N", "0", "Y", "1", "-", "o"))
def test_parser_does_not_invent_semantics_for_undocumented_cancel_markers(marker):
    item = ACTIVE_ITEM.replace("<cdealType> </cdealType>", f"<cdealType>{marker}</cdealType>")
    with pytest.raises(_parse_error()):
        _parse(_response(item))


@pytest.mark.parametrize("code", ("000", "00"))
def test_parser_explicit_success_zero_has_checked_receipt(code):
    batch = _parse(_response("", code=code))
    assert batch["rows"] == []
    assert batch["receipt"]["total_count"] == batch["receipt"]["cancelled_count"] == 0
    assert batch["receipt"]["api_result_code"] == code


@pytest.mark.parametrize(("tag", "bad_value"), (
    ("aptNm", ""), ("dealAmount", ""), ("dealAmount", "0"), ("dealAmount", "-1"),
    ("dealAmount", "83,00"), ("dealAmount", "83000.5"), ("dealAmount", "nan"),
    ("excluUseAr", ""), ("excluUseAr", "0"), ("excluUseAr", "-59"),
    ("excluUseAr", "NaN"), ("excluUseAr", "Infinity"), ("excluUseAr", "1e309"),
    ("dealYear", ""), ("dealYear", "26"), ("dealMonth", "0"), ("dealMonth", "13"),
    ("dealMonth", "7"), ("dealDay", "0"), ("dealDay", "32"), ("sggCd", "11680"),
    ("cdealType", "unknown"), ("cdealDay", "not-a-date"), ("cdealDay", "-"), ("cdealDay", "20260230"),
    ("cdealDay", "20260801"),
))
def test_parser_rejects_invalid_record_instead_of_dropping_or_approximating(tag, bad_value):
    item = ET.fromstring(ACTIVE_ITEM)
    item.find(tag).text = bad_value
    with pytest.raises(_parse_error()):
        _parse(_response(ET.tostring(item, encoding="unicode")))


@pytest.mark.parametrize("tag", ("dealAmount", "excluUseAr", "dealYear", "dealMonth", "dealDay",
                                 "sggCd", "cdealType", "cdealDay"))
def test_parser_missing_critical_field_is_failure_not_verified_legacy(tag):
    item = ET.fromstring(ACTIVE_ITEM)
    item.remove(item.find(tag))
    with pytest.raises(_parse_error()):
        _parse(_response(ET.tostring(item, encoding="unicode")))


@pytest.mark.parametrize("xml", (
    "not XML", b"\xff", "<response>", "<html><body>error</body></html>",
    "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>",
    _response("").replace("<resultCode>000</resultCode>", ""),
    _response("", code="30"), _response(ACTIVE_ITEM, total=2),
    _response(ACTIVE_ITEM, total=0), _response(ACTIVE_ITEM, page=2),
    _response(ACTIVE_ITEM, size=0),
    _response(ACTIVE_ITEM).replace("<totalCount>1</totalCount>", "<totalCount>bad</totalCount>"),
    _response(ACTIVE_ITEM).replace("<cdealType> </cdealType>", "<cdealType/><cdealType>O</cdealType>"),
), ids=("not-xml", "invalid-utf8", "unclosed-xml", "html-error", "openapi-error", "missing-code",
        "api-error", "incomplete-page", "count-mismatch", "wrong-page", "invalid-size", "bad-count",
        "duplicate-cancel-field"))
def test_parser_rejects_malformed_api_and_incomplete_pages(xml):
    with pytest.raises(_parse_error()):
        _parse(xml)


@pytest.mark.parametrize(("lawd", "ym"), (
    ("", "202608"), ("1111", "202608"), ("11110x", "202608"),
    ("11110", "20268"), ("11110", "202600"), ("11110", "202613"),
))
def test_parser_rejects_invalid_request_scope(lawd, ym):
    with pytest.raises(_parse_error()):
        _parse(_response(""), lawd, ym)


def test_parser_rejects_entire_batch_when_a_later_active_or_cancelled_item_is_invalid():
    invalid = ACTIVE_ITEM.replace("<dealAmount>83,000</dealAmount>", "<dealAmount>-1</dealAmount>")
    for bad in (invalid, invalid.replace("<cdealType> </cdealType>", "<cdealType>O</cdealType>")):
        with pytest.raises(_parse_error()):
            _parse(_response(ACTIVE_ITEM + bad))


def test_parser_error_does_not_echo_payload_or_api_error_message():
    marker = "synthetic-private-error-message"
    xml = _response("", code="30").replace("Fixture", marker)
    with pytest.raises(_parse_error()) as error:
        _parse(xml)
    assert marker not in str(error.value) and "<" not in str(error.value)


def test_parser_rejects_items_outside_the_declared_items_container():
    xml = _response("").replace("</body>", ACTIVE_ITEM + "</body>")
    with pytest.raises(_parse_error()):
        _parse(xml)


def test_parser_validates_the_locally_captured_https_response_without_network():
    captured = PROJECT / "report/molit-cancellation/raw/11110-202608-p1.xml"
    if not captured.is_file():
        pytest.skip("Local HTTPS capture not installed; synthetic regressions still run")
    batch = _parse(captured.read_bytes())
    assert batch["receipt"]["total_count"] == batch["receipt"]["accepted_count"] == 22
    assert batch["receipt"]["cancelled_count"] == 0
    assert batch["receipt"]["response_sha256"] == "62fb96946a3819f4d7bf22b96b5e446273f0746e0dfab70b4bdbf4bb09efff74"
    assert all(row["cancellation_checked"] is True and row["ym"] == "202608" for row in batch["rows"])


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_collectors_batch_api_and_rolling_twelve_complete_months(script):
    namespace, _opener = _collector_functions(script, _response(ACTIVE_ITEM))
    batch = namespace["fetch_batch"]("11110", "202608")
    assert len(batch["rows"]) == 1 and batch["receipt"]["accepted_count"] == 1
    namespace["date"] = types.SimpleNamespace(today=lambda: date(2026, 9, 26))
    assert namespace["_rolling_months"]() == ["202509", "202510", "202511", "202512", "202601", "202602",
                                             "202603", "202604", "202605", "202606", "202607", "202608"]


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_collectors_send_key_only_to_https_endpoint(script):
    tree = ast.parse((PROJECT / script).read_text(encoding="utf-8"))
    endpoints = [node.value.value for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "EP" for target in node.targets)]
    assert len(endpoints) == 1 and endpoints[0].startswith("https://apis.data.go.kr/")


def _cache_main_context(script, cache=None):
    namespace, opener = _collector_functions(script, _response(ACTIVE_ITEM))
    writes = []

    def memory_open(_path, mode="r"):
        if mode == "w":
            destination = io.StringIO()
            writes.append(destination)
            return destination
        return io.StringIO(json.dumps(cache))

    namespace.update(json=json, open=Mock(side_effect=memory_open), print=Mock(),
                     os=types.SimpleNamespace(path=types.SimpleNamespace(exists=lambda _path: cache is not None)),
                     LAWD={"합성구": "11110"}, MONTHS=["202608"], OUT="/synthetic/no-real-cache.json")
    return namespace, opener, writes


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_main_rejects_legacy_done_cache_before_any_network_or_write(script):
    cache = {"11110": [{"apt": "합성단지", "area": 59.5, "price": 830_000_000, "ym": "202608"}],
             "_done": ["11110|202608"]}
    namespace, opener, writes = _cache_main_context(script, cache)
    with pytest.raises(_parse_error(), match="^MOLIT_CACHE_UNVERIFIED$"):
        namespace["main"]()
    opener.assert_not_called()
    assert writes == []
    namespace["print"].assert_not_called()


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_main_without_cache_starts_fresh_and_stores_checked_rows(script):
    namespace, opener, writes = _cache_main_context(script)
    namespace["main"]()
    assert opener.call_count == 1 and len(writes) == 1
    saved = json.loads(writes[0].getvalue())
    assert saved["_done"] == ["11110|202608"]
    assert saved["11110"][0]["cancellation_checked"] is True


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_main_validated_cache_preserves_resume_without_refetch(script):
    cache = {"11110": _parse(_response(ACTIVE_ITEM))["rows"], "_done": ["11110|202608"]}
    namespace, opener, writes = _cache_main_context(script, cache)
    namespace["main"]()
    opener.assert_not_called()
    assert len(writes) == 1
    assert json.loads(writes[0].getvalue()) == cache


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
@pytest.mark.parametrize("markers", ("missing", "empty", "partial"))
def test_main_rejects_checked_rows_without_done_marker_before_duplicate_refetch(script, markers):
    cache = {"11110": _parse(_response(ACTIVE_ITEM))["rows"]}
    if markers == "empty":
        cache["_done"] = []
    elif markers == "partial":
        cache["11110"] += _checked_month_rows("11110", "202607")
        cache["_done"] = ["11110|202607"]
    namespace, opener, writes = _cache_main_context(script, cache)
    with pytest.raises(_parse_error(), match="^MOLIT_CACHE_UNVERIFIED$"):
        namespace["main"]()
    opener.assert_not_called()
    assert writes == []
    namespace["print"].assert_not_called()


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
@pytest.mark.parametrize("defect", (
    "missing-checked", "false-checked", "missing-provenance", "wrong-schema", "wrong-source",
    "cancel-flag", "cancel-day", "wrong-gu", "wrong-month", "missing-hash", "wrong-deal-date", "bool-price",
))
def test_main_rejects_missing_or_tampered_cache_evidence_before_side_effects(script, defect):
    row = copy.deepcopy(_parse(_response(ACTIVE_ITEM))["rows"][0])
    if defect == "missing-checked":
        row.pop("cancellation_checked")
    elif defect == "false-checked":
        row["cancellation_checked"] = False
    elif defect == "missing-provenance":
        row.pop("provenance")
    elif defect == "wrong-deal-date":
        row["deal_date"] = "2026-07-12"
    elif defect == "bool-price":
        row["price"] = True
    else:
        field, value = {"wrong-schema": ("schema", "wrong"), "wrong-source": ("source", "wrong"),
                        "cancel-flag": ("cdeal_type", "O"), "cancel-day": ("cdeal_day", "20260901"),
                        "wrong-gu": ("lawd_cd", "11680"), "wrong-month": ("request_ym", "202607"),
                        "missing-hash": ("response_sha256", "")}[defect]
        row["provenance"][field] = value
    namespace, opener, writes = _cache_main_context(script, {"11110": [row], "_done": ["11110|202608"]})
    with pytest.raises(_parse_error(), match="^MOLIT_CACHE_UNVERIFIED$"):
        namespace["main"]()
    opener.assert_not_called()
    assert writes == []


def _checked_month_rows(lawd, ym):
    item = ET.fromstring(ACTIVE_ITEM)
    item.find("sggCd").text = lawd
    item.find("dealYear").text, item.find("dealMonth").text = ym[:4], str(int(ym[4:]))
    return _parse(_response(ET.tostring(item, encoding="unicode")), lawd, ym)["rows"]


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_main_resume_retains_only_current_districts_and_twelve_complete_months(script):
    months = ["202509", "202510", "202511", "202512", "202601", "202602",
              "202603", "202604", "202605", "202606", "202607", "202608"]
    cache = {"11110": [row for ym in ["202508", *months] for row in _checked_month_rows("11110", ym)],
             "11680": _checked_month_rows("11680", "202608"),
             "_done": [f"11110|{ym}" for ym in ["202508", *months]] + ["11680|202608"]}
    namespace, opener, writes = _cache_main_context(script, cache)
    namespace["MONTHS"] = months
    namespace["main"]()
    opener.assert_not_called()
    saved = json.loads(writes[-1].getvalue())
    assert set(saved) == {"11110", "_done"}
    assert [row["ym"] for row in saved["11110"]] == months
    assert saved["_done"] == [f"11110|{ym}" for ym in months]


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_main_completed_empty_cache_is_unverified_not_an_authenticated_api_zero(script):
    namespace, opener, writes = _cache_main_context(script, {"11110": [], "_done": ["11110|202608"]})
    with pytest.raises(_parse_error(), match="^MOLIT_CACHE_UNVERIFIED$"):
        namespace["main"]()
    opener.assert_not_called()
    assert writes == []


@pytest.mark.parametrize("script", FETCH_SCRIPTS)
def test_main_fresh_explicit_api_zero_preserves_success_contract(script):
    namespace, opener, writes = _cache_main_context(script)
    opener.side_effect = lambda *_args, **_kwargs: io.BytesIO(_response("").encode())
    namespace["main"]()
    assert opener.call_count == 1 and len(writes) == 1
    saved = json.loads(writes[0].getvalue())
    assert saved == {"11110": [], "_done": ["11110|202608"]}
