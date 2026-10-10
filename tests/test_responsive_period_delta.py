"""Inline periodic tables retain complete rows in a narrow two-column layout."""
from bs4 import BeautifulSoup

from blog.period_delta import _tbl


def test_inline_period_table_stacks_all_fields_without_losing_values_or_links():
    headers = ["단지", "기준 기간", "이번 기간", "표본 n", "출처"]
    rows = [["강남 긴 단지 이름", "2026-09-01", "2026-10-01", "37건", '<a href="https://rt.molit.go.kr/">국토부 원문</a>'],
            ["노원 두 번째 단지", "2025-09-01", "2025-10-01", "48건", "공식 공개 자료"]]
    soup = BeautifulSoup(_tbl(headers, rows, True), "html.parser")
    trs = soup.find_all("tr")
    assert all(len(tr.find_all("td", recursive=False)) == 2 for tr in trs)
    for tr, row in zip(trs[1:], rows, strict=True):
        cells = tr.find_all("td", recursive=False)
        assert cells[0].get_text() == row[0]
        for header, raw in zip(headers[1:], row[1:], strict=True):
            value = BeautifulSoup(raw, "html.parser").get_text()
            assert cells[1].get_text().count(header) == 1
            assert cells[1].get_text().count(value) == 1
    assert soup.find("a")["href"] == "https://rt.molit.go.kr/"
    assert "table-layout:fixed" in soup.table["style"]
    assert "word-break:keep-all" in trs[1].td["style"]


def test_site_period_table_keeps_comparison_columns():
    soup = BeautifulSoup(_tbl(["구", "기간", "표본"], [["강남", "9월", "20"]], False), "html.parser")
    assert len(soup.find_all("th")) == 3
    assert len(soup.find_all("td")) == 3
