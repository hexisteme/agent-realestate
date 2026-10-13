"""MOLIT 수집기 확장(2026-10-14 승인): 법정동·지번·층·거래유형·단지일련번호, 전월세 계약구분 보존."""
from __future__ import annotations

import ast
import io
import types
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from blog.molit_transactions import (MolitTradeResponseError, parse_molit_rent_rows,
                                     parse_molit_trade_response, validate_molit_trade_cache)

PROJECT = Path(__file__).resolve().parents[1]
_BASE = ("<aptNm>합성단지</aptNm><excluUseAr>84.92</excluUseAr><dealAmount>150,000</dealAmount>"
         "<dealYear>2026</dealYear><dealMonth>8</dealMonth><dealDay>12</dealDay><sggCd>11590</sggCd>"
         "<cdealType> </cdealType><cdealDay> </cdealDay>")
_EXTRA = ("<umdNm>대방동</umdNm><jibun>1131</jibun><floor>12</floor>"
          "<dealingGbn>중개거래</dealingGbn><aptSeq>11590-100</aptSeq>")


def _trade(items: str) -> str:
    n = items.count("<item>")
    return (f"<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header>"
            f"<body><items>{items}</items><totalCount>{n}</totalCount><pageNo>1</pageNo>"
            f"<numOfRows>4000</numOfRows></body></response>")


def _rows(extra: str) -> list[dict]:
    return parse_molit_trade_response(_trade(f"<item>{_BASE}{extra}</item>"), "11590", "202608")["rows"]


def test_trade_row_keeps_identity_and_bias_fields():
    row = _rows(_EXTRA)[0]
    assert (row["umd"], row["jibun"], row["floor"], row["dealing"], row["apt_seq"]) == (
        "대방동", "1131", 12, "중개거래", "11590-100")
    assert (row["apt"], row["area"], row["price"], row["ym"], row["deal_date"]) == (
        "합성단지", 84.92, 1_500_000_000, "202608", "2026-08-12")


def test_trade_row_absent_tags_are_none():
    row = _rows("")[0]
    assert all(row[k] is None for k in ("umd", "jibun", "floor", "dealing", "apt_seq"))


def test_trade_row_blank_values_are_none_and_basement_floor_is_negative():
    blank = _rows("<umdNm> </umdNm><jibun></jibun><floor> </floor><dealingGbn></dealingGbn><aptSeq></aptSeq>")[0]
    assert all(blank[k] is None for k in ("umd", "jibun", "floor", "dealing", "apt_seq"))
    assert _rows("<floor>-1</floor>")[0]["floor"] == -1


@pytest.mark.parametrize("floor", ["3층", "B1", "1.5", "지하1"])
def test_trade_row_unparsed_floor_is_none_not_batch_failure(floor):
    """G6 agy: 보조 필드 한 칸 오기로 한 달 배치가 버려지면 수집 완결성이 깨진다 — 층은 None 으로 남긴다."""
    row = _rows(f"<floor>{floor}</floor>")[0]
    assert row["floor"] is None and row["price"] == 1_500_000_000


@pytest.mark.parametrize("extra, code", [
    ("<umdNm>대방동</umdNm><umdNm>본동</umdNm>", "MOLIT_FIELD_STRUCTURE_INVALID"),
    ("<aptSeq><x>1</x></aptSeq>", "MOLIT_FIELD_STRUCTURE_INVALID"),
])
def test_trade_row_malformed_fields_fail_closed(extra, code):
    with pytest.raises(MolitTradeResponseError, match=code):
        _rows(extra)


def _cache(row: dict) -> dict:
    return {"11590": [row], "_done": ["11590|202608"]}


def test_cache_accepts_legacy_rows_without_new_fields():
    row = _rows(_EXTRA)[0]
    legacy = {k: v for k, v in row.items() if k not in ("umd", "jibun", "floor", "dealing", "apt_seq")}
    validate_molit_trade_cache(_cache(legacy))
    validate_molit_trade_cache(_cache(row))


@pytest.mark.parametrize("key, bad", [("floor", "12"), ("floor", True), ("umd", 5), ("apt_seq", ["x"]),
                                      ("dealing", 1), ("jibun", 1131)])
def test_cache_rejects_wrong_field_types(key, bad):
    row = {**_rows(_EXTRA)[0], key: bad}
    with pytest.raises(MolitTradeResponseError, match="MOLIT_CACHE_UNVERIFIED"):
        validate_molit_trade_cache(_cache(row))


def _rent_item(deposit="50,000", monthly="0", ctype="신규", extra=""):
    return (f"<item><aptNm>합성단지</aptNm><excluUseAr>59.97</excluUseAr><deposit>{deposit}</deposit>"
            f"<monthlyRent>{monthly}</monthlyRent><contractType>{ctype}</contractType><umdNm>대방동</umdNm>"
            f"<jibun>1131</jibun><floor>-1</floor><aptSeq>11590-100</aptSeq>{extra}</item>")


def _rent(items: str) -> str:
    return (f"<response><header><resultCode>000</resultCode></header>"
            f"<body><items>{items}</items></body></response>")


def test_rent_rows_keep_contract_type_and_identity():
    rows = parse_molit_rent_rows(_rent(_rent_item() + _rent_item(ctype="갱신")), "202608")
    assert [r["contract_type"] for r in rows] == ["신규", "갱신"]
    assert rows[0] == {"apt": "합성단지", "area": 59.97, "price": 500_000_000, "ym": "202608",
                       "contract_type": "신규", "umd": "대방동", "jibun": "1131", "floor": -1,
                       "apt_seq": "11590-100"}


def test_rent_rows_keep_legacy_filters():
    xml = _rent(_rent_item(monthly="30") + _rent_item(deposit="") + _rent_item(monthly=""))
    rows = parse_molit_rent_rows(xml, "202608")
    assert len(rows) == 1 and rows[0]["price"] == 500_000_000


def test_rent_rows_blank_or_unknown_values():
    xml = _rent("<item><aptNm>합성단지</aptNm><excluUseAr>59.97</excluUseAr><deposit>1,000</deposit>"
                "<monthlyRent>0</monthlyRent><contractType> </contractType><floor>지하</floor></item>")
    row = parse_molit_rent_rows(xml, "202608")[0]
    assert (row["contract_type"], row["floor"], row["umd"], row["apt_seq"]) == (None, None, None, None)


def test_jeonse_collector_fetch_uses_shared_rent_parser():
    """수집 스크립트의 fetch 본문을 그대로 실행 — 인라인 파싱이 남아 새 필드를 버리면 실패."""
    script = PROJECT / "fetch_molit_jeonse_recent_25gu.py"
    tree = ast.parse(script.read_text(encoding="utf-8"))
    fetch = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fetch"]
    assert len(fetch) == 1
    payload = _rent(_rent_item(ctype="갱신")).encode("utf-8")
    opener = Mock(side_effect=lambda *_a, **_k: io.BytesIO(payload))
    ns = {"urllib": types.SimpleNamespace(parse=urllib.parse, request=types.SimpleNamespace(urlopen=opener)),
          "time": types.SimpleNamespace(sleep=Mock()), "ET": ET, "date": date,
          "K": "synthetic-fixture-key", "EP": "https://fixture.invalid/rent",
          "parse_molit_rent_rows": parse_molit_rent_rows}
    exec(compile(ast.Module(body=fetch, type_ignores=[]), str(script), "exec"), ns)
    rows = ns["fetch"]("11590", "202608")
    assert rows and rows[0]["contract_type"] == "갱신" and rows[0]["apt_seq"] == "11590-100"


def test_rent_rows_reject_api_error_header():
    """G6 Codex: resultCode 오류 + 빈 items 가 정상 0건으로 완료 표시되던 경로."""
    err = "<response><header><resultCode>22</resultCode></header><body><items></items></body></response>"
    with pytest.raises(ValueError):
        parse_molit_rent_rows(err, "202608")
    ok = "<response><header><resultCode>000</resultCode></header><body><items></items></body></response>"
    assert parse_molit_rent_rows(ok, "202608") == []


@pytest.mark.parametrize("xml", [
    "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>",
    "<response><body><items></items></body></response>",
])
def test_rent_rows_reject_non_success_envelopes(xml):
    """R3 Codex: 성공 헤더가 없는 오류 envelope 도 정상 0건으로 완료 표시되면 안 된다."""
    with pytest.raises(ValueError):
        parse_molit_rent_rows(xml, "202608")
