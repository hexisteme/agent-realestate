"""은행연합회 월별 COFIX 공식 공시표 — 금리는 %, 날짜는 실제 공시일."""
from __future__ import annotations

import re
from datetime import date
from html.parser import HTMLParser
from typing import Callable


COFIX_URL = "https://portal.kfb.or.kr/fingoods/cofix.php"
COFIX_ENCODING = "cp949"  # 공식 응답 charset=euc-kr; 기존 fetch_text는 errors=replace.
_PARSE_ERROR = "은행연합회 COFIX 공개 표를 해석할 수 없음"
_FETCH_ERROR = "은행연합회 COFIX 공개 표 수집 실패"
_HEADERS = ("공시일", "대상월", "신규취급액기준COFIX", "잔액기준COFIX", "신잔액기준COFIX")
_UNIT = re.compile(r"단위\s*[:：]?\s*([%A-Za-z가-힣]+)")
_DATE = re.compile(r"\d{4}/\d{2}/\d{2}")
_MONTH = re.compile(r"\d{4}/\d{2}")
_RATE = re.compile(r"\d+(?:\.\d+)?")


class CofixResponseError(ValueError):
    """고정 오류만 노출하며 응답·전송 오류 원문을 포함하지 않는다."""


class _CofixTables(HTMLParser):
    """월별/단기 표와 인접 단위 문구를 분리해 수집한다."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.outside_text = ""
        self.rows = None
        self.row = None
        self.cell = None
        self.context = ""
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.rows is not None:
                self.invalid = True
                return
            self.rows = []
            self.context = self.outside_text
            self.outside_text = ""
            self.invalid = False
        elif tag == "tr" and self.rows is not None:
            self.row = []
        elif tag in ("th", "td") and self.row is not None:
            attributes = dict(attrs)
            if any(attributes.get(name, "1") != "1" for name in ("colspan", "rowspan")):
                self.invalid = True
            self.cell = []

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)
        elif self.rows is None:
            self.outside_text = (self.outside_text + text)[-1000:]

    def handle_endtag(self, tag):
        if tag in ("th", "td") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.rows is not None:
            self.tables.append((self.context, self.rows, self.invalid))
            self.rows = None
            self.outside_text = ""


def _parse_cofix_table(text: str) -> dict[str, list[tuple[str, float]]]:
    parser = _CofixTables()
    parser.feed(text)
    parser.close()
    monthly = []
    for context, rows, invalid in parser.tables:
        if not rows:
            continue
        headings = [re.sub(r"\s+", "", cell) for cell in rows[0]]
        if "대상월" in headings or "신규취급액기준COFIX" in headings:
            monthly.append((context, rows, headings, invalid))
    if parser.rows is not None or len(monthly) != 1:
        raise CofixResponseError(_PARSE_ERROR)
    context, rows, headings, invalid = monthly[0]
    units = _UNIT.findall(context)
    if invalid or len(headings) != len(_HEADERS) or set(headings) != set(_HEADERS) or not units or units[-1] != "%":
        raise CofixResponseError(_PARSE_ERROR)
    columns = {heading: headings.index(heading) for heading in _HEADERS}
    observations = {}
    for row in rows[1:]:
        if len(row) != len(headings) or any("\ufffd" in cell for cell in row):
            raise CofixResponseError(_PARSE_ERROR)
        publication_text = row[columns["공시일"]]
        month_text = row[columns["대상월"]]
        if not _DATE.fullmatch(publication_text) or not _MONTH.fullmatch(month_text):
            raise CofixResponseError(_PARSE_ERROR)
        publication = date.fromisoformat(publication_text.replace("/", "-"))
        reference = date.fromisoformat(month_text.replace("/", "-") + "-01")
        if reference >= publication.replace(day=1):
            raise CofixResponseError(_PARSE_ERROR)
        rates = []
        for heading in ("신규취급액기준COFIX", "잔액기준COFIX", "신잔액기준COFIX"):
            value_text = row[columns[heading]]
            if not _RATE.fullmatch(value_text):
                raise CofixResponseError(_PARSE_ERROR)
            value = float(value_text)
            if not 0 <= value <= 100:
                raise CofixResponseError(_PARSE_ERROR)
            rates.append(value)
        day = publication.isoformat()
        if day in observations and observations[day] != tuple(rates):
            raise CofixResponseError(_PARSE_ERROR)
        observations[day] = tuple(rates)
    if not observations:
        raise CofixResponseError(_PARSE_ERROR)
    return {
        "cofix_new": [(day, values[0]) for day, values in sorted(observations.items())],
        "cofix_bal": [(day, values[1]) for day, values in sorted(observations.items())],
    }


def parse_cofix_html(text: str) -> dict[str, list[tuple[str, float]]]:
    """월별 표 → 공시일 오름차순 신규취급액/잔액 금리(%); 대상월은 날짜로 반환하지 않는다."""
    try:
        return _parse_cofix_table(text)
    except Exception:
        raise CofixResponseError(_PARSE_ERROR) from None


def fetch_cofix_series(fetch: Callable[..., str]) -> dict[str, list[tuple[str, float]]]:
    """기존 injected fetch_text 전송으로 공개표를 읽는다. 이전 네이버 시계열은 병합하지 않는다."""
    try:
        text = fetch(COFIX_URL, encoding=COFIX_ENCODING)
    except Exception:
        raise CofixResponseError(_FETCH_ERROR) from None
    return parse_cofix_html(text)
