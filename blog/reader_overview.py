"""A small reader-facing layer over already collected housing observations."""
from __future__ import annotations

from datetime import date
import html
import math
import re

from blog.housing_support import _context, _format_application_period
from blog.market_indicators import READER_INDICATORS


READER_OVERVIEW_CSS = """
.reader-overview{margin:16px 0 24px;word-break:keep-all;overflow-wrap:anywhere}
.reader-overview>p{margin:8px 0 14px;color:#536674}
.reader-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.reader-card{container-type:inline-size;min-width:0;padding:20px;border:1px solid #dce5e8;border-radius:14px;background:white}
.reader-card h2{margin:0 0 12px;font-size:19px;line-height:1.5}
.reader-price-pair{display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
.reader-price-pair>div{min-width:0}
@media(min-width:600px){@container(min-width:416px){.reader-price-pair{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}}}
.reader-card p{margin:8px 0;color:#526675;font-size:14px}
.reader-notice{padding:10px 0;border-top:1px solid #e2e8e6}
.reader-notice a{font-weight:650;line-height:1.55}
.reader-value{font-size:26px;font-weight:750;color:#173c36;font-variant-numeric:tabular-nums}
.reader-meta{font-size:13px;color:#536674;display:block;margin:4px 0}
.reader-row{display:grid;grid-template-columns:72px minmax(0,1fr) 86px;gap:10px;align-items:center;margin:9px 0;font-size:14px}
.reader-row strong{text-align:right;font-size:14px;white-space:nowrap}
.reader-track{height:12px;background:#e8efed;border-radius:3px;overflow:hidden}
.reader-track i{display:block;background:#6a9e91;height:100%}
.reader-nav{display:flex;gap:12px;flex-wrap:wrap;margin-top:14px}
.reader-overview a,.reader-depth>summary{min-height:44px;display:inline-flex;align-items:center;padding:8px 6px}
.reader-depth{border-top:1px solid #cbd8dd;margin-top:20px}
.reader-depth>summary{display:list-item;cursor:pointer;font-size:17px;font-weight:700;padding:14px 0}
@media(max-width:760px){.reader-grid{grid-template-columns:minmax(0,1fr)}.reader-card{padding:16px}.reader-row{gap:6px;grid-template-columns:68px minmax(0,1fr) 82px}}
"""


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    except OverflowError:
        return False


def _observations(macro, today):
    """Recheck the narrow visual input: never revive excluded raw indicators."""
    snapshot = macro.get("visual_snapshot") if isinstance(macro, dict) else None
    if not isinstance(snapshot, dict):
        return {}
    current, stamp = _date(today), _date(snapshot.get("asof"))
    if not current or not stamp or not 0 <= (current - stamp).days <= 7:
        return {}
    raw = snapshot.get("indicators")
    if not isinstance(raw, dict):
        return {}
    accepted = {}
    for code in ("kr_mortgage_new", "seoul_apt_sale", "seoul_apt_jeonse", "seoul_apt_sales"):
        item = raw.get(code)
        if not isinstance(item, dict):
            continue
        observed = _date(item.get("date"))
        if not observed or observed > stamp or not 0 <= (current - observed).days <= 100:
            continue
        if item.get("freq") != "monthly" or not _number(item.get("value")):
            continue
        unit = item.get("unit")
        if code in {"seoul_apt_sale", "seoul_apt_jeonse"}:
            match = re.fullmatch(r"(\d{4})\.(\d{2})=100", unit) if isinstance(unit, str) else None
            base = _date(f'{match[1]}-{match[2]}-01') if match else None
            if not base or base > observed:
                continue
        elif unit != ("%" if code == "kr_mortgage_new" else "호"):
            continue
        if unit == '호' and item['value'] != int(item['value']):
            continue
        accepted[code] = item
    return accepted


def _value(item):
    value = item["value"]
    return f"{value:,.0f}" if item["unit"] == "호" else f"{value:g}"


def _recent(item, limit):
    points = {}
    conflicts = set()
    series = item.get("series")
    for row in series if isinstance(series, list) else []:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            continue
        day = _date(row[0])
        if day and day <= _date(item["date"]) and _number(row[1]):
            if item['unit'] != '호' or row[1] == int(row[1]):
                key = row[0]
                if key in conflicts:
                    continue
                if key in points and points[key] != row[1]:
                    conflicts.add(key)
                    del points[key]
                else:
                    points[key] = row[1]
    points[item["date"]] = item["value"]
    return sorted(points.items())[-limit:]


def _support_notices(ctx):
    """Use validated notices and the brief's status order, one per issuer."""
    status_rank = {'접수중': 0, '예정': 1, '미확인': 2, '마감': 3}
    ordered = sorted(ctx['notices'], key=lambda item: (status_rank[item['status']],
                     item.get('apply_start') or '9999-12-31', item['published_on']))
    selected = []
    for issuer in ('sh', 'lh'):
        item = next((row for row in ordered if row['issuer'] == issuer), None)
        if item is None:
            selected.append(f'<p>{issuer.upper()} 대표 공고 미확인 · 공식 목록을 확인하세요.</p>')
            continue
        selected.append(f'<div class="reader-notice" data-reader-notice="{issuer}">'
                        f'<span class="reader-meta">{issuer.upper()} · {html.escape(item["status"])}</span>'
                        f'<a href="{html.escape(item["url"],quote=True)}">{html.escape(item["title"])}</a>'
                        f'<p>신청 {_format_application_period(item,ctx["today"])}</p></div>')
    return ''.join(selected)


def _reading(code, observations, *, site):
    item = observations.get(code)
    if not item:
        return '<p>미수집</p>'
    unit = item["unit"]
    suffix = " 지수" if "=100" in unit else unit
    label = f'{_value(item)}{suffix}'
    meta = item["date"][:7] + (f' · {unit}' if "=100" in unit else '')
    source_url = READER_INDICATORS[code][3]
    if not site:
        if code == 'seoul_apt_sales':
            recent = _recent(item, 3)
            peak = max(v for _, v in recent)
            return ''.join(f'<span data-reader-trade-month="{day[:7]}">{day[:7]} · <b>{v:,.0f}호</b><br>'
                           f'<span style="display:inline-block;background:#6a9e91;height:8px;width:{v/peak*100 if peak else 0:.1f}%"></span></span><br>'
                           for day,v in recent) + '막대는 0 기준<br>'
        style = ' style="font-size:26px"' if code == 'kr_mortgage_new' else ''
        return f'<p><b{style}>{label}</b> · {meta}</p>'
    recent = _recent(item, 3 if code == 'seoul_apt_sales' else 2)
    peak = max(v for _, v in recent)
    bars = ''.join(
        f'<div class="reader-row"><span>{day[:7]}</span><span class="reader-track" aria-hidden="true">'
        f'<i style="width:{(value / peak * 100) if peak else 0:.2f}%"></i></span>'
        f'<strong>{value:,.0f}호</strong></div>' if unit == '호' else
        f'<div class="reader-row"><span>{day[:7]}</span><span class="reader-track" aria-hidden="true">'
        f'<i style="width:{(value / peak * 100) if peak else 0:.2f}%"></i></span>'
        f'<strong>{value:g}{"%" if unit == "%" else ""}</strong></div>'
        for day, value in recent)
    return (f'<div class="reader-value">{label}</div><span class="reader-meta">{meta}</span>'
            f'{bars}<span class="reader-meta">최근 관측 · 막대는 0 기준</span>'
            f'<a href="{source_url}">공식 출처 ↗</a>')


def build_reader_overview(macro, support, *, today: str, asof: str) -> dict[str, str]:
    """Render read-only HTML; all detailed observations remain in the caller."""
    observations = _observations(macro, today)
    if not _date(asof) or not _date(today) or _date(asof) > _date(today):
        observations = {}
    current = today if _date(today) else '1970-01-01'
    ctx = _context(support, current)
    support_line = ' · '.join(f'{issuer.upper()} {ctx["states"][issuer]}' for issuer in ('sh', 'lh'))
    result = {}
    for site in (False, True):
        cards = []
        items = (
            ('rate', '① 새 주택담보대출 금리', _reading('kr_mortgage_new', observations, site=site),
             '전국·예금은행·신규취급 평균입니다. 개인 대출금리는 다릅니다.'),
            ('prices', '② 서울 매매·전세 흐름',
             ('<div class="reader-price-pair"><div>' if site else '')
             + '<p><b>매매</b></p>' + _reading('seoul_apt_sale', observations, site=site)
             + ('</div><div>' if site else '')
             + '<p><b>전세</b></p>' + _reading('seoul_apt_jeonse', observations, site=site)
             + ('</div></div>' if site else ''),
             '각 기준월의 가격을 100으로 두고 비교한 가격지수입니다.'),
            ('trades', '③ 서울 아파트 거래량', _reading('seoul_apt_sales', observations, site=site),
             '신고월 기준 동(호)수입니다. 매물 표시건수와 구분하세요.'),
            ('support', '④ 주거지원 확인하기', (f'<p><b>{support_line}</b> · 공고 확인 {ctx["day"]}</p>'
             + (_support_notices(ctx) if site else '')),
             '연령·소득·자산·무주택 조건과 접수시각은 공고별 확인이 필요합니다.'),
        )
        for key, title, body, explanation in items:
            if site:
                cards.append(f'<section class="reader-card" data-reader-card="{key}"><h2>{title}</h2>{body}<p>{explanation}</p></section>')
            else:
                static_body = body.replace('<p>', '').replace('</p>', '<br>')
                cards.append(f'<h4>{title}</h4><p data-reader-card="{key}" style="font-size:14px">'
                             f'{static_body}{explanation}</p>')
        date_text = html.escape(asof if _date(asof) and _date(today) and _date(asof) <= _date(today) else '미확인')
        intro = f'<h3>서울 집값과 주거지원, 한눈에</h3><p>실거래 자료 {date_text} · 지표마다 기준일이 다릅니다.</p>'
        if site:
            intro = '<p>대출금리부터 매매·전세 흐름, 주거지원 공고까지 확인하세요. 지표마다 기준일이 다릅니다.</p>'
            from blog.build_site import BASE_URL
            links = (f'<nav class="reader-nav"><a href="{BASE_URL}/explorer.html">예산·지역별 실거래</a>'
                     f'<a href="{BASE_URL}/macro.html">금리·전체 지표</a>'
                     f'<a href="{BASE_URL}/housing-support.html">SH·LH 공고·조건</a></nav>')
            result['site_html'] = '<section class="reader-overview" data-reader-overview="story">' + intro + '<div class="reader-grid">' + ''.join(cards) + '</div>' + links + '</section>'
        else:
            result['tistory_html'] = '<p data-reader-overview="story">금리 → 가격 → 거래 → 지원</p>' + intro + ''.join(cards)
    return result
