"""Strict MOLIT apartment-sale response boundary; no transport or cache writes."""
from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from datetime import date


class MolitTradeResponseError(ValueError):
    """Safe reason code only: never echo an API message, key, URL, or row."""


def _read_molit_text(element: ET.Element, tag: str) -> str:
    matches = element.findall(tag)
    if len(matches) != 1 or len(matches[0]):
        raise MolitTradeResponseError("MOLIT_FIELD_STRUCTURE_INVALID")
    return (matches[0].text or "").strip()


def _read_molit_count(element: ET.Element, tag: str, minimum: int = 0) -> int:
    value = _read_molit_text(element, tag)
    if not re.fullmatch(r"[0-9]+", value):
        raise MolitTradeResponseError("MOLIT_PAGE_COUNT_INVALID")
    count = int(value)
    if count < minimum:
        raise MolitTradeResponseError("MOLIT_PAGE_COUNT_INVALID")
    return count


def _read_molit_deal_date(item: ET.Element) -> date:
    values = [_read_molit_text(item, tag) for tag in ("dealYear", "dealMonth", "dealDay")]
    if not re.fullmatch(r"[0-9]{4}", values[0]) or any(
            not re.fullmatch(r"[0-9]{1,2}", value) for value in values[1:]):
        raise MolitTradeResponseError("MOLIT_DEAL_DATE_INVALID")
    try:
        return date(*(int(value) for value in values))
    except ValueError:
        raise MolitTradeResponseError("MOLIT_DEAL_DATE_INVALID") from None


def _read_molit_cancel_date(value: str) -> date | None:
    if value == "":
        return None
    if re.fullmatch(r"[0-9]{8}", value):
        parts = (value[:4], value[4:6], value[6:])
    elif re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        parts = tuple(value.split("-"))
    elif re.fullmatch(r"[0-9]{4}\.[0-9]{2}\.[0-9]{2}", value):
        parts = tuple(value.split("."))
    elif re.fullmatch(r"[0-9]{2}\.[0-9]{2}\.[0-9]{2}", value):
        short = value.split(".")
        parts = ("20" + short[0], short[1], short[2])
    else:
        raise MolitTradeResponseError("MOLIT_CANCEL_DATE_INVALID")
    try:
        return date(*(int(part) for part in parts))
    except ValueError:
        raise MolitTradeResponseError("MOLIT_CANCEL_DATE_INVALID") from None


def parse_molit_trade_response(xml: bytes | str, lawd_cd: str, request_ym: str) -> dict:
    """Validate one complete month response, exclude cancellations, return rows/receipt.

    Legacy apt/area/price/ym fields remain unchanged. The requested month is
    independently checked against every item's actual contract date. A successful
    zero is possible only with a normal API header and explicit totalCount=0.
    Missing cancellation fields are not retroactively declared checked.
    """
    if not isinstance(lawd_cd, str) or not re.fullmatch(r"[0-9]{5}", lawd_cd):
        raise MolitTradeResponseError("MOLIT_REQUEST_SCOPE_INVALID")
    if not isinstance(request_ym, str) or not re.fullmatch(r"[0-9]{6}", request_ym):
        raise MolitTradeResponseError("MOLIT_REQUEST_SCOPE_INVALID")
    try:
        date(int(request_ym[:4]), int(request_ym[4:]), 1)
    except ValueError:
        raise MolitTradeResponseError("MOLIT_REQUEST_SCOPE_INVALID") from None
    try:
        raw = xml.encode("utf-8") if isinstance(xml, str) else xml
        if not isinstance(raw, bytes):
            raise MolitTradeResponseError("MOLIT_XML_INVALID")
        text = raw.decode("utf-8")
        if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
            raise MolitTradeResponseError("MOLIT_XML_INVALID")
        root = ET.fromstring(raw)
    except (UnicodeError, ET.ParseError):
        raise MolitTradeResponseError("MOLIT_XML_INVALID") from None
    # Namespace-qualified XML retains the same field contract.
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    if root.tag != "response" or len(root.findall("header")) != 1 or len(root.findall("body")) != 1:
        raise MolitTradeResponseError("MOLIT_RESPONSE_STRUCTURE_INVALID")
    header, body = root.find("header"), root.find("body")
    result_code = _read_molit_text(header, "resultCode")
    if result_code not in ("000", "00"):
        raise MolitTradeResponseError("MOLIT_API_ERROR")
    total = _read_molit_count(body, "totalCount")
    page = _read_molit_count(body, "pageNo", 1)
    size = _read_molit_count(body, "numOfRows", 1)
    containers = body.findall("items")
    if len(containers) > 1:
        raise MolitTradeResponseError("MOLIT_RESPONSE_STRUCTURE_INVALID")
    items = list(containers[0]) if containers else []
    if any(item.tag != "item" for item in items) or len(list(root.iter("item"))) != len(items):
        raise MolitTradeResponseError("MOLIT_RESPONSE_STRUCTURE_INVALID")
    if page != 1 or total != len(items) or size < len(items):
        raise MolitTradeResponseError("MOLIT_PAGE_INCOMPLETE")
    source_sha256 = hashlib.sha256(raw).hexdigest()
    rows, cancelled_count = [], 0
    for item in items:
        name = _read_molit_text(item, "aptNm")
        amount = _read_molit_text(item, "dealAmount")
        area_text = _read_molit_text(item, "excluUseAr")
        if not name:
            raise MolitTradeResponseError("MOLIT_COMPLEX_NAME_INVALID")
        if not re.fullmatch(r"(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)", amount):
            raise MolitTradeResponseError("MOLIT_PRICE_INVALID")
        price = int(amount.replace(",", "")) * 10_000
        if price <= 0:
            raise MolitTradeResponseError("MOLIT_PRICE_INVALID")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", area_text):
            raise MolitTradeResponseError("MOLIT_AREA_INVALID")
        area = float(area_text)
        if not math.isfinite(area) or area <= 0:
            raise MolitTradeResponseError("MOLIT_AREA_INVALID")
        deal_date = _read_molit_deal_date(item)
        if f"{deal_date.year:04d}{deal_date.month:02d}" != request_ym:
            raise MolitTradeResponseError("MOLIT_DEAL_MONTH_MISMATCH")
        if _read_molit_text(item, "sggCd") != lawd_cd:
            raise MolitTradeResponseError("MOLIT_DEAL_LAWD_MISMATCH")
        cancel_type = _read_molit_text(item, "cdealType")
        cancel_day = _read_molit_text(item, "cdealDay")
        if cancel_type not in ("", "O"):
            raise MolitTradeResponseError("MOLIT_CANCEL_FLAG_INVALID")
        cancelled_on = _read_molit_cancel_date(cancel_day)
        if cancelled_on is not None and cancelled_on < deal_date:
            raise MolitTradeResponseError("MOLIT_CANCEL_DATE_INVALID")
        if cancel_type == "O" or cancelled_on is not None:
            cancelled_count += 1
            continue
        rows.append({"apt": name, "area": area, "price": price, "ym": request_ym,
                     "deal_date": deal_date.isoformat(), "cancellation_checked": True,
                     "provenance": {"schema": "MolitTradeRow/v1", "source": "MOLIT_RTMS_APT_TRADE_DEV",
                                    "lawd_cd": lawd_cd, "request_ym": request_ym,
                                    "response_sha256": source_sha256,
                                    "cdeal_type": cancel_type, "cdeal_day": cancel_day}})
    return {"rows": rows, "receipt": {
        "schema": "MolitTradeBatch/v1", "source": "MOLIT_RTMS_APT_TRADE_DEV",
        "request_lawd": lawd_cd, "request_ym": request_ym, "response_sha256": source_sha256,
        "api_result_code": result_code, "total_count": total, "accepted_count": len(rows),
        "cancelled_count": cancelled_count, "cancellation_filter_applied": True}}


def validate_molit_trade_cache(cache: dict) -> None:
    """Reject unverified resume rows/zero markers before caller side effects.

    This checks evidence structure, not API authenticity. Original raw responses
    still require independent replay. A completed empty month has no row evidence
    in the legacy cache schema, so it cannot be authenticated by a _done marker.
    """
    try:
        if not isinstance(cache, dict) or not isinstance(cache.get("_done", []), list):
            raise ValueError
        verified_pairs = set()
        for lawd_cd, rows in cache.items():
            if lawd_cd == "_done":
                continue
            if not isinstance(lawd_cd, str) or not re.fullmatch(r"[0-9]{5}", lawd_cd) or not isinstance(rows, list):
                raise ValueError
            for row in rows:
                if not isinstance(row, dict) or row.get("cancellation_checked") is not True:
                    raise ValueError
                provenance = row.get("provenance")
                if not isinstance(provenance, dict) or any((
                    provenance.get("schema") != "MolitTradeRow/v1",
                    provenance.get("source") != "MOLIT_RTMS_APT_TRADE_DEV",
                    provenance.get("lawd_cd") != lawd_cd,
                    provenance.get("request_ym") != row.get("ym"),
                    provenance.get("cdeal_type") != "",
                    provenance.get("cdeal_day") != "",
                )):
                    raise ValueError
                digest = provenance.get("response_sha256")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    raise ValueError
                ym, deal_day = row.get("ym"), row.get("deal_date")
                if not isinstance(ym, str) or not re.fullmatch(r"[0-9]{6}", ym):
                    raise ValueError
                if not isinstance(deal_day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", deal_day):
                    raise ValueError
                observed_day = date.fromisoformat(deal_day)
                if f"{observed_day.year:04d}{observed_day.month:02d}" != ym:
                    raise ValueError
                name, price, area = row.get("apt"), row.get("price"), row.get("area")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError
                if not isinstance(price, int) or isinstance(price, bool) or price <= 0:
                    raise ValueError
                if not isinstance(area, (int, float)) or isinstance(area, bool) or not math.isfinite(area) or area <= 0:
                    raise ValueError
                verified_pairs.add(f"{lawd_cd}|{ym}")
        if any(not isinstance(marker, str) or marker not in verified_pairs for marker in cache.get("_done", [])):
            raise ValueError
        if verified_pairs.difference(cache.get("_done", [])):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise MolitTradeResponseError("MOLIT_CACHE_UNVERIFIED") from None
