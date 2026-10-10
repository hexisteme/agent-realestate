"""Compact source-linked observations for readers of the daily housing blog."""
from __future__ import annotations

import html

from blog.macro_entry import macro_click_attribute

HOUSING_CODES = ("kr_mortgage_new", "seoul_apt_sale", "seoul_apt_jeonse", "seoul_apt_sales", "seoul_unsold")
READER_INDICATORS = {
    "bok_base": ("한국은행 기준금리", "정책금리 수준", "한국은행", "https://www.bok.or.kr/portal/singl/baseRate/list.do?dataSeCd=01&menuNo=200643"),
    "cofix_new": ("신규취급액 코픽스", "변동형 주담대의 기준금리", "은행연합회", "https://portal.kfb.or.kr/fingoods/cofix.php"),
    "kr_govt3y": ("국고채 3년 금리", "국내 시장금리 수준", "한국은행 ECOS", "https://ecos.bok.or.kr/"),
    "fed_target_hi": ("미 연방기금 목표범위", "미국 정책금리 범위", "FRED", "https://fred.stlouisfed.org/series/DFEDTARU"),
    "us10y": ("미 국채 10년 금리", "해외 장기금리 수준", "FRED", "https://fred.stlouisfed.org/series/DGS10"),
    "kr_mortgage_new": ("주택담보대출 금리", "전국·예금은행·신규취급 평균", "한국은행 ECOS", "https://ecos.bok.or.kr/"),
    "seoul_apt_sale": ("서울 아파트 매매가격지수", "아파트 매매가격 흐름·월간", "한국부동산원·ECOS", "https://ecos.bok.or.kr/"),
    "seoul_apt_jeonse": ("서울 아파트 전세가격지수", "아파트 전세가격 흐름·월간", "한국부동산원·ECOS", "https://ecos.bok.or.kr/"),
    "seoul_apt_sales": ("서울 아파트 매매거래량", "매매 신고 동(호)수·월간", "한국부동산원 R-ONE", "https://www.reb.or.kr/r-one/portal/stat/easyStatPage.do"),
    "seoul_unsold": ("서울 미분양 주택", "전체 주택 판매 재고·월말", "국토교통부·ECOS", "https://ecos.bok.or.kr/"),
}

MARKET_LAYOUT_CSS = """
.market-observations{min-width:0;max-width:100%;word-break:keep-all;overflow-wrap:normal}
.market-observations .market-table-scroll{max-width:100%;overflow-x:auto;border:1px solid #dce3eb;border-radius:10px}
.market-observations table.market-desktop-table{width:100%;min-width:880px;table-layout:auto;border-collapse:collapse;font-size:14px}
.market-observations table.market-full-table{min-width:1150px}
.market-observations .market-desktop-table th,.market-observations .market-desktop-table td{padding:12px;vertical-align:top;text-align:left;white-space:normal;word-break:keep-all;overflow-wrap:normal;border-bottom:1px solid #e2e8f0}
.market-observations .market-desktop-table th{background:#f3f6fa;color:#526477;font-weight:600}
.market-observations .market-desktop-table th:first-child,.market-observations .market-desktop-table td:first-child{min-width:190px;width:190px}
.market-observations .market-number{white-space:nowrap;font-variant-numeric:tabular-nums}
.market-observations .market-meta{min-width:130px;max-width:260px;white-space:normal;word-break:keep-all;overflow-wrap:normal}
.market-observations .market-date{color:#64748b;font-size:12px;white-space:nowrap}
.market-observations .market-mobile-cards{display:none}
.market-reader-table{word-break:keep-all;overflow-wrap:normal}
@media(max-width:800px){
 .market-observations .market-table-scroll{display:none}
 .market-observations .market-mobile-cards{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
 .market-observations .market-mobile-card{min-width:0;padding:16px;background:#fff;border:1px solid #dce3eb;border-radius:12px}
 .market-observations .market-mobile-card h3{font-size:17px;margin:0 0 6px;line-height:1.5;word-break:keep-all}
 .market-observations .market-mobile-card p{margin:0 0 8px;white-space:normal;word-break:keep-all}
 .market-observations .market-mobile-card dl{display:grid;grid-template-columns:86px minmax(0,1fr);gap:8px;margin:12px 0 0;font-size:13px;line-height:1.6}
 .market-observations .market-mobile-card dt{color:#64748b}
 .market-observations .market-mobile-card dd{margin:0;white-space:normal;word-break:keep-all;overflow-wrap:normal}
}
"""


def render_observation_views(cards: list[dict], *, full: bool) -> str:
    """One visible representation per viewport, preserving the complete card metadata."""
    def escape(value):
        return html.escape(str(value or "—"), quote=True)

    def delta(value):
        lead, sep, tail = str(value or "—").partition("(")
        return f'<span class="market-number">{escape(lead)}</span>' + (f' <span>({escape(tail)}</span>' if sep else "")

    headings = ["지표(기준일)", "값", "직전 대비"]
    if full:
        headings.append("같은 값 시작")
    headings += ["1년 전 대비"]
    if full:
        headings.append("연속")
    headings.append("다음 발표")
    if full:
        headings.append("출처")
    rows, mobile = [], []
    for card in cards:
        link = f'<a href="{escape(card["url"])}" {macro_click_attribute(card["code"])}>{escape(card["source"])}</a>'
        label = f'<a href="{escape(card["url"])}" {macro_click_attribute(card["code"])}>{escape(card["label"])}</a>'
        cells = [f'{label}<br><span class="market-date">{escape(card["date"])}</span>',
                 f'<b class="market-number">{escape(card["value_txt"])}</b>', delta(card["delta_prev_txt"])]
        if full:
            cells.append(escape(card.get("since_txt")))
        cells.append(delta(card["delta_12m_txt"]))
        if full:
            cells.append(escape(card.get("streak_txt")))
        cells.append(escape(card.get("next_release_txt")))
        if full:
            cells.append(link)
        rows.append('<tr>' + ''.join(f'<td class="market-meta">{cell}</td>' for cell in cells) + '</tr>')
        details = [
            ("직전 대비", delta(card["delta_prev_txt"])),
            ("1년 전 대비", delta(card["delta_12m_txt"])),
            ("같은 값 시작", escape(card.get("since_txt"))),
            ("연속", escape(card.get("streak_txt"))),
            ("다음 발표", escape(card.get("next_release_txt"))), ("출처", link)]
        mobile.append('<article class="market-mobile-card">'
                      f'<h3>{escape(card["label"])}</h3><p><b class="market-number">{escape(card["value_txt"])}</b> '
                      f'<span class="market-date">{escape(card["date"])}</span></p><dl>'
                      + ''.join(f'<dt>{name}</dt><dd>{value}</dd>' for name, value in details) + '</dl></article>')
    table_class = "market-desktop-table" + (" market-full-table" if full else "")
    return (f'<style>{MARKET_LAYOUT_CSS}</style><section class="market-observations">'
            '<div class="market-table-scroll" tabindex="0" role="region" aria-label="거시지표 비교표 — 가로 스크롤">'
            f'<table class="{table_class}"><thead><tr>' + ''.join(f'<th scope="col">{h}</th>' for h in headings)
            + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
            '<div class="market-mobile-cards">' + ''.join(mobile) + '</div></section>')


def _source_label(source: str) -> str:
    verified = {
        "한국은행 ECOS 901Y149/01/a7": "한국부동산원·ECOS",
        "한국은행 ECOS 901Y150/01/a7": "한국부동산원·ECOS",
        "한국은행 ECOS 901Y074/I410B": "국토교통부·ECOS",
        "한국은행 기준금리 추이(bok.or.kr)": "한국은행",
        "은행연합회 공식 COFIX 공시표": "은행연합회 공식표",
    }
    if source in verified:
        return verified[source]
    if source.startswith("한국은행 ECOS "):
        return "한국은행 ECOS"
    if source.startswith("FRED "):
        return "FRED"
    if source.startswith("한국부동산원 R-ONE "):
        return "한국부동산원 R-ONE"
    return source


def render_reader_table(ctx: dict, codes: tuple[str, ...], *, site: bool) -> str:
    """Two columns fit a narrow blog column; unavailable cards retain their labels."""
    by = {card["code"]: card for card in ctx.get("cards", [])}
    states = ctx.get("indicator_states", {})
    rows = []
    for code in codes:
        label, purpose, source, url = READER_INDICATORS[code]
        card = by.get(code)
        status = states.get(code, "missing")
        value = card["value_txt"] if card else ("기준월 오래됨" if status == "stale" else "자료 미수집")
        observed = card["date"] if card else "기준일 미확인"
        delta = card["delta_prev_txt"].split("(")[0] if card else ""
        if card:
            # The observation's source, including any fallback, is authoritative.
            url = card["url"]
            source = _source_label(card["source"])
        event = " " + macro_click_attribute(code) if site else ""
        cell_style = "padding:8px;white-space:normal" + (";text-align:left" if site else "")
        rows.append(
            f'<tr style="border-bottom:1px solid #ddd;vertical-align:top"><td style="{cell_style}">'
            f'<b>{html.escape(label)}</b><br><span style="font-size:12px;color:#64748b">{html.escape(purpose)}<br>'
            f'<a href="{html.escape(url, quote=True)}"{event}>{html.escape(source)}</a></span></td>'
            f'<td style="{cell_style}">'
            f'<b style="font-size:16px">{html.escape(value)}</b><br><span style="font-size:12px;color:#64748b">'
            f'{html.escape(observed)}{(" · " + html.escape(delta)) if delta and delta != "—" else ""}</span></td></tr>'
        )
    table_class = ' class="market-reader-table"' if site else ""
    return (f'<table{table_class} style="width:100%;table-layout:fixed;border-collapse:collapse;font-size:14px;word-break:keep-all">'
            '<tr><td style="width:58%;padding:8px;background:#f8fafc;color:#64748b;font-size:12px">지표 · 확인할 내용</td>'
            '<td style="width:42%;padding:8px;background:#f8fafc;color:#64748b;font-size:12px">값 · 기준일</td></tr>'
            + "".join(rows) + '</table>')


def render_housing_indicators(ctx: dict, *, site: bool) -> str:
    title = '<h2>집을 알아볼 때 함께 볼 지표 5가지</h2>' if site else '<p><b>집을 알아볼 때 함께 볼 지표 5가지</b></p>'
    return (title + render_reader_table(ctx, HOUSING_CODES, site=site)
            + '<p style="font-size:12px;color:#64748b">주담대는 개인의 적용금리와 다릅니다. 가격지수는 개별 단지 가격이 아니며 '
              '기준월=100인 상대 지수입니다. 거래량은 신고월 기준으로 계약월 통계와 다릅니다. '
              '미분양은 준공 전·후를 포함한 전체 주택 수입니다.</p>')
