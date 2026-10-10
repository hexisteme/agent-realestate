"""Official Woori Bank daily gold basis prices, with observed dates and units.

The public quote table reports both KRW/g and its reference USD/troy ounce.
Neither the customer buy/sell prices nor an FX-derived approximation is used.
"""
from __future__ import annotations

from datetime import date, timedelta
from html.parser import HTMLParser
import math
import re
from urllib.parse import urlencode

GOLD_PAGE = "https://spot.wooribank.com/pot/Dream?withyou=POGLD0006"
GOLD_QUERY = "https://spot.wooribank.com/pot/jcc"
GOLD_SOURCE_KRW = "우리은행 공식 골드뱅킹 일별 기준가격"
GOLD_SOURCE_USD = "우리은행 공식 골드뱅킹 기준 국제 금 시세"
_CAPTION = "금가격 조회 결과 정보"
_ERROR = "공식 금 가격표 확인 불가"


class _GoldTables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self.table = self.row = self.cell = None
        self.depth = 0
        self.in_caption = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self.table = {"caption": [], "rows": [], "invalid": False}
            else:
                self.table["invalid"] = True
        if self.depth != 1:
            return
        if tag == "caption":
            self.in_caption = True
        elif tag == "tr":
            self.row = []
        elif tag in ("th", "td") and self.row is not None:
            self.cell = []
            if any(key in ("rowspan", "colspan") and value != "1" for key, value in attrs):
                self.table["invalid"] = True
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data):
        if self.depth == 1 and self.in_caption:
            self.table["caption"].append(data)
        elif self.depth == 1 and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if self.depth == 1:
            if tag in ("th", "td") and self.cell is not None and self.row is not None:
                self.row.append(" ".join("".join(self.cell).split()))
                self.cell = None
            elif tag == "tr" and self.row is not None:
                self.table["rows"].append(self.row)
                self.row = None
            elif tag == "caption":
                self.in_caption = False
        if tag == "table" and self.depth:
            if self.depth == 1:
                self.tables.append(self.table)
                self.table = self.row = self.cell = None
            self.depth -= 1


def _gold_price(text):
    if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        raise ValueError(_ERROR)
    value = float(text.replace(",", ""))
    if not math.isfinite(value) or value <= 0:
        raise ValueError(_ERROR)
    return value


def parse_woori_gold(html: str) -> dict[str, list[tuple[str, float]]]:
    """Read only the one captioned, unit-bound daily basis-price table."""
    parser = _GoldTables()
    parser.feed(html)
    parser.close()
    tables = [table for table in parser.tables if " ".join("".join(table["caption"]).split()) == _CAPTION]
    if len(tables) != 1 or tables[0]["invalid"] or len(tables[0]["rows"]) < 2:
        raise ValueError(_ERROR)
    rows = tables[0]["rows"]
    headings = [re.sub(r"\s+", "", text) for text in rows[0]]
    columns = []
    for alternatives in (("일자",), ("기준가격(원/g)",),
                         ("금가격(USD/트로이온즈)", "금가격(USD/트로이온스)")):
        matches = [i for i, heading in enumerate(headings) if heading in alternatives]
        if len(matches) != 1:
            raise ValueError(_ERROR)
        columns.append(matches[0])
    observed = {}
    for row in rows[1:]:
        if len(row) != len(headings) or not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", row[columns[0]]):
            raise ValueError(_ERROR)
        try:
            stamp = date.fromisoformat(row[columns[0]].replace(".", "-")).isoformat()
        except ValueError:
            raise ValueError(_ERROR) from None
        values = (_gold_price(row[columns[1]]), _gold_price(row[columns[2]]))
        if stamp in observed and observed[stamp] != values:
            raise ValueError(_ERROR)
        observed[stamp] = values
    return {code: [(stamp, values[i]) for stamp, values in sorted(observed.items())]
            for i, code in enumerate(("gold_krw_g", "gold_usd_oz"))}


def fetch_woori_gold(today: str, fetch) -> dict[str, list[tuple[str, float]]]:
    """A bounded public GET, keeping the bank's actual latest observation date."""
    try:
        target = date.fromisoformat(today)
        if target.isoformat() != today:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError(_ERROR) from None
    start = target - timedelta(days=400)
    query = urlencode({"withyou": "POGLD0006", "__ID": "c007327", "BAS_SDT": start.strftime("%Y%m%d"),
                       "BAS_EDT": target.strftime("%Y%m%d"), "term": "day"})
    try:
        body = fetch(f"{GOLD_QUERY}?{query}", "utf-8")
    except Exception:
        raise ValueError("공식 금 가격표 전송 실패") from None
    series = parse_woori_gold(body)
    dates = [date.fromisoformat(stamp) for stamp, _ in series["gold_krw_g"]]
    if any(stamp < start or stamp > target for stamp in dates) or (target - dates[-1]).days > 7:
        raise ValueError(_ERROR)
    return series
