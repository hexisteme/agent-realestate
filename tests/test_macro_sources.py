"""Official KFB COFIX table parsing and fixed-error transport contract."""
from __future__ import annotations

import importlib
import traceback
from pathlib import Path

import pytest


FIXTURE = Path(__file__).parent / "fixtures" / "macro" / "kfb_cofix.html"
HEADERS = ["공시일", "대상월", "신규취급액기준 COFIX", "잔액기준 COFIX", "신 잔액기준 COFIX"]
ROW = ["2026/09/15", "2026/08", "3.18", "3.05", "2.44"]
PARSE_ERROR = "은행연합회 COFIX 공개 표를 해석할 수 없음"
FETCH_ERROR = "은행연합회 COFIX 공개 표 수집 실패"


def source():
    return importlib.import_module("agent_realestate.collectors.macro_sources")


def table(headers=HEADERS, rows=None, unit="%"):
    rows = [ROW] if rows is None else rows
    head = "".join(f"<th>{cell}</th>" for cell in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<p>단위 : {unit}</p><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def test_official_fixture_returns_publication_dates_and_percent_rates():
    result = source().parse_cofix_html(FIXTURE.read_text(encoding="utf-8"))
    assert set(result) == {"cofix_new", "cofix_bal"}
    assert result["cofix_new"][-1] == ("2026-09-15", 3.18)
    assert result["cofix_bal"][-1] == ("2026-09-15", 3.05)
    assert len(result["cofix_new"]) == len(result["cofix_bal"]) == 9
    assert result["cofix_new"] == sorted(result["cofix_new"])
    assert ("2026-08-01", 3.18) not in result["cofix_new"]


def test_reordered_columns_are_selected_by_heading():
    order = [1, 3, 4, 0, 2]
    result = source().parse_cofix_html(table([HEADERS[i] for i in order], [[ROW[i] for i in order]]))
    assert result == {"cofix_new": [("2026-09-15", 3.18)], "cofix_bal": [("2026-09-15", 3.05)]}


def test_short_term_table_is_ignored():
    short = table(["공시일", "대상기간", "단기 COFIX"], [["2026/09/30", "2026/09/21~2026/09/27", "7.99"]])
    result = source().parse_cofix_html(short + table())
    assert result["cofix_new"] == [("2026-09-15", 3.18)]


def test_unrelated_layout_table_spans_do_not_reject_monthly_table():
    layout = '<table><tr><td colspan="2">다른 안내 표</td></tr></table>'
    assert source().parse_cofix_html(layout + table())["cofix_bal"] == [("2026-09-15", 3.05)]


def test_nested_markup_whitespace_and_entities_are_read_as_cell_text():
    wrapped = [f"<span> {cell.replace(' ', '&nbsp;')} </span>" for cell in HEADERS]
    result = source().parse_cofix_html(table(wrapped, [[f"<b>{c}</b>" for c in ROW]]))
    assert result["cofix_bal"] == [("2026-09-15", 3.05)]


@pytest.mark.parametrize("broken", [
    "<html>no COFIX table</html>",
    table(HEADERS[:3], [ROW[:3]]),
    table([*HEADERS[:3], "신 잔액기준 COFIX", "신 잔액기준 COFIX"]),
    table(rows=[]),
    table(rows=[ROW[:4]]),
    table(rows=[[*ROW, "6.99"]]),
    table().replace("<th>공시일</th>", '<th colspan="2">공시일</th>'),
    table(rows=[["2026/02/30", *ROW[1:]]]),
    table(rows=[["2026/09", *ROW[1:]]]),
    table(rows=[[ROW[0], "2026/13", *ROW[2:]]]),
    table(rows=[[ROW[0], "2026/09", *ROW[2:]]]),
    table(rows=[[ROW[0], ROW[1], "NaN", *ROW[3:]]]),
    table(rows=[[ROW[0], ROW[1], "318", *ROW[3:]]]),
    table(rows=[[ROW[0], ROW[1], "", *ROW[3:]]]),
    table(rows=[[ROW[0], ROW[1], "-", *ROW[3:]]]),
    table(rows=[[ROW[0], ROW[1], "3.18�", *ROW[3:]]]),
    table(unit="bp"),
    table() + table(),
    table(rows=[ROW, [*ROW[:2], "3.19", *ROW[3:]]]),
])
def test_missing_corrupt_or_ambiguous_monthly_table_raises_fixed_error(broken):
    with pytest.raises(ValueError, match=f"^{PARSE_ERROR}$"):
        source().parse_cofix_html(broken)


def test_each_table_has_its_own_unit_context():
    short = table(["공시일", "대상기간", "단기 COFIX"], [["2026/09/30", "2026/09/21~2026/09/27", "7.99"]])
    with pytest.raises(ValueError, match=f"^{PARSE_ERROR}$"):
        source().parse_cofix_html(short + table(unit="bp"))


def test_identical_publication_rows_are_deduplicated():
    result = source().parse_cofix_html(table(rows=[ROW, ROW]))
    assert result["cofix_new"] == [("2026-09-15", 3.18)]


def test_fetch_uses_official_url_and_cp949_with_existing_transport():
    calls = []
    def fetch(url, encoding="utf-8"):
        calls.append((url, encoding))
        return FIXTURE.read_text(encoding="utf-8")
    result = source().fetch_cofix_series(fetch)
    assert calls == [("https://portal.kfb.or.kr/fingoods/cofix.php", "cp949")]
    assert result["cofix_bal"][-1] == ("2026-09-15", 3.05)


def test_fetch_failures_do_not_expose_upstream_details():
    def fetch(url, encoding):
        raise RuntimeError("PRIVATE-UPSTREAM-CONTENT")
    with pytest.raises(ValueError, match=f"^{FETCH_ERROR}$") as failure:
        source().fetch_cofix_series(fetch)
    assert "PRIVATE-UPSTREAM-CONTENT" not in "".join(traceback.format_exception(failure.value))


def test_fetch_invalid_body_uses_fixed_parser_error():
    with pytest.raises(ValueError, match=f"^{PARSE_ERROR}$"):
        source().fetch_cofix_series(lambda url, encoding: "not a table")
