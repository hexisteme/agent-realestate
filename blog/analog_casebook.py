"""닮은국면사례집(AnalogCasebook) — analogs.html. 지금과 6개 지표 구간이 k개 이상 같았던 과거 판정월의 12·36개월 뒤
서울 아파트 매매지수 변화를 "비중복 사례 n건 중 m건 + 사례 전부"로 보인다. DIKW 검토 2026-10-30 P3(케이스2).

규칙 정본은 결과 계산 전에 커밋한 `docs/analog-casebook-prereg-v1.md`(PREREG v1) — 아래 상수를 바꾸면 판을 올린다.
- 축 6개를 판정월 t 에 관측월 t−L(공표 시차)의 값으로, 그 축의 확장 창 3분위(유효값 60개 이상)로 낮음·중간·높음.
- 닮은 달 = 현재 판정월과 같은 구간 축 ≥ k(판정 불가 축은 불일치). 주 k=5, k=4·6 병기.
- 결과 = 판정월에 알려진 마지막 가격 관측월 b=t−1 대비 b+h 의 변화율(h=12 주·36 보조), ±0.5% 안은 보합.
- 비중복 사례 = 대표월 사이 36개월 이상. 독립이 아니다. 사례 < 5 면 문장 없이 표만.
전망·인과·권유 없음, '확률' 단어 금지. 과거 자료판이 없어 최신 개정값으로 다시 분류한 사례임을 고지한다.
"""
from __future__ import annotations

import html
import statistics
from dataclasses import dataclass, field

from blog.wording_guard import assert_lead_wording_ok, assert_wording_ok

PREREG_VERSION = "v1"
PREREG_PATH = "docs/analog-casebook-prereg-v1.md"
MIN_WINDOW = 60
K_MAIN = 5
K_ALT = (4, 6)
HORIZONS = (12, 36)
FLAT_PCT = 0.5
CASE_GAP_M = 36
MIN_CASES = 5
CHAIN_MAX_SPREAD = 0.01
CHAIN_MIN_OVERLAP = 12
BANDS = ("낮음", "중간", "높음")
_EXTRA_WORDS = ("확률", "전망", "예상", "것이다", "때문에", "추천", "유망", "급등", "급락", "지금과 같은 시장")


@dataclass(frozen=True)
class Axis:
    key: str
    label: str
    source: str        # raw 키
    transform: str     # pct12 | diff12 | level | sum36
    lag: int           # 공표 시차(개월)
    unit: str


AXES = (
    Axis("A1", "가격 흐름(서울 아파트 매매지수 12개월 변화율)", "price", "pct12", 1, "%"),
    Axis("A2", "대출 금리(신규 주택담보대출 평균)", "mortgage_rate", "level", 1, "%"),
    Axis("A3", "시장 금리(국고채 3년 12개월 변화)", "govt3y", "diff12", 1, "%p"),
    Axis("A4", "유동성(M2 12개월 증가율)", "m2", "pct12", 2, "%"),
    Axis("A5", "서울 가계대출(예금은행 12개월 증가율)", "seoul_loans", "pct12", 2, "%"),
    Axis("A6", "공급(서울 주택 인허가 직전 36개월 합)", "permits_monthly", "sum36", 2, "호"),
)


class ChainRefused(ValueError):
    """겹침이 짧거나 겹침 비율 폭이 CHAIN_MAX_SPREAD 를 넘어 연쇄하지 않는다."""


# ── 월 산수 ─────────────────────────────────────────────────────────────────

def _m(month: str) -> int:
    y, mo = month[:7].split("-")
    return int(y) * 12 + int(mo) - 1


def _s(n: int) -> str:
    return f"{n // 12:04d}-{n % 12 + 1:02d}"


def shift_month(month: str, k: int) -> str:
    return _s(_m(month) + k)


def to_monthly(series) -> dict[str, float]:
    """[(YYYY-MM-DD, 값)] 또는 {YYYY-MM: 값} → {YYYY-MM: 값}."""
    items = series.items() if isinstance(series, dict) else series
    return {str(d)[:7]: float(v) for d, v in items}


# ── 입력 정리 ───────────────────────────────────────────────────────────────

def chain_price_index(old: dict[str, float], new: dict[str, float]) -> tuple[dict[str, float], dict]:
    """연쇄지수(ChainedIndex) — old 끝 이후의 new 값을 겹침 평균 비율(old/new)로 곱해 잇는다. 작성자 연쇄."""
    overlap = sorted(set(old) & set(new))
    if len(overlap) < CHAIN_MIN_OVERLAP:
        raise ChainRefused(f"겹침 {len(overlap)}개월 < {CHAIN_MIN_OVERLAP}")
    ratios = [old[m] / new[m] for m in overlap]
    spread = max(ratios) / min(ratios) - 1
    if spread > CHAIN_MAX_SPREAD:
        raise ChainRefused(f"겹침 비율 폭 {spread:.2%} > {CHAIN_MAX_SPREAD:.0%}")
    factor = statistics.fmean(ratios)
    last_old = max(old)
    out = dict(old)
    out.update({m: v * factor for m, v in new.items() if m > last_old})
    return out, {"overlap_from": overlap[0], "overlap_to": overlap[-1], "n_overlap": len(overlap),
                 "factor": factor, "spread": spread, "joined_from": shift_month(last_old, 1)}


def monthly_from_ytd(ytd: dict[str, float]) -> dict[str, float]:
    """연초 누계 → 월 값. 1월은 그대로, 그 밖은 같은 해 전월 누계와의 차(전월 없으면 비운다)."""
    out = {}
    for m, v in ytd.items():
        if m.endswith("-01"):
            out[m] = v
        elif (prev := shift_month(m, -1)) in ytd:
            out[m] = v - ytd[prev]
    return out


def transform_series(series: dict[str, float], how: str) -> dict[str, float]:
    out = {}
    for m, v in series.items():
        if how == "level":
            out[m] = v
        elif how in ("pct12", "diff12"):
            base = series.get(shift_month(m, -12))
            if base is None or (how == "pct12" and base == 0):
                continue
            out[m] = (v / base - 1) * 100 if how == "pct12" else v - base
        elif how == "sum36":
            window = [series.get(shift_month(m, -i)) for i in range(36)]
            if all(x is not None for x in window):
                out[m] = sum(window)
        else:
            raise ValueError(how)
    return out


def prepare_inputs(raw: dict) -> tuple[dict[str, dict[str, float]], dict]:
    """raw(수집 스냅샷 series) → (축별 변환 시계열 {축키: {관측월: 값}}, 가격 연쇄 정보 + 가격지수)."""
    price, chain = chain_price_index(to_monthly(raw["price_old"]), to_monthly(raw["price_new"]))
    base = {"price": price, "mortgage_rate": to_monthly(raw["mortgage_rate"]), "govt3y": to_monthly(raw["govt3y"]),
            "m2": to_monthly(raw["m2"]), "seoul_loans": to_monthly(raw["seoul_loans"]),
            "permits_monthly": monthly_from_ytd(to_monthly(raw["permits_ytd"]))}
    return {a.key: transform_series(base[a.source], a.transform) for a in AXES}, {"chain": chain, "price": price}


# ── 구간·닮음 ───────────────────────────────────────────────────────────────

def classify_axis_band(window: list[float], value: float) -> tuple[str, float, float] | None:
    """지표구간(AxisBand) — 확장 창(값 포함) inclusive 3분위. 유효값 < MIN_WINDOW 이면 None(판정 불가)."""
    if len(window) < MIN_WINDOW:
        return None
    q1, q2 = statistics.quantiles(window, n=3, method="inclusive")
    band = BANDS[0] if value <= q1 else BANDS[1] if value <= q2 else BANDS[2]
    return band, q1, q2


def judge_month_bands(axes_series: dict[str, dict[str, float]], t: str) -> list[dict | None]:
    """판정월 t 의 축별 {band, value, q1, q2, obs}. 관측월 = t − 공표 시차."""
    out = []
    for a in AXES:
        s = axes_series[a.key]
        obs = shift_month(t, -a.lag)
        if obs not in s:
            out.append(None)
            continue
        window = [v for m, v in s.items() if m <= obs]
        got = classify_axis_band(window, s[obs])
        out.append(None if got is None else {"band": got[0], "value": s[obs], "q1": got[1], "q2": got[2], "obs": obs})
    return out


def current_judge_month(axes_series: dict[str, dict[str, float]]) -> str:
    """모든 축의 관측이 있는 가장 최근 판정월 = 축마다 (마지막 관측월 + 시차) 의 최소."""
    return _s(min(_m(max(axes_series[a.key])) + a.lag for a in AXES))


def find_analog_months(bands: dict[str, list], current: str, k: int) -> list[str]:
    """닮은달(AnalogMonth) — current 와 같은 구간 축이 k 개 이상인 과거 판정월(판정 불가 축은 불일치)."""
    cur = bands[current]
    out = []
    for t in sorted(bands):
        if t >= current:
            continue
        same = sum(1 for b, c in zip(bands[t], cur) if b is not None and c is not None and b["band"] == c["band"])
        if same >= k:
            out.append(t)
    return out


def group_nonoverlapping_cases(months: list[str]) -> list[dict]:
    """비중복사례(NonOverlappingCase) — 첫 닮은 달이 대표월, 대표월 + CASE_GAP_M 개월 안의 닮은 달은 그 사례에 묶는다."""
    cases: list[dict] = []
    for t in sorted(months):
        if cases and _m(t) < _m(cases[-1]["rep"]) + CASE_GAP_M:
            cases[-1]["members"].append(t)
        else:
            cases.append({"rep": t, "members": [t]})
    return cases


def outcome_pct(price: dict[str, float], t: str, h: int) -> float | None:
    """판정월 t 에 알려진 마지막 가격 관측월 b=t−1 대비 b+h 의 변화율(%). 아직 없으면 None(미도래)."""
    b = shift_month(t, -1)
    end = shift_month(b, h)
    if b not in price or end not in price:
        return None
    return (price[end] / price[b] - 1) * 100


def direction(pct: float) -> str:
    return "상승" if pct > FLAT_PCT else "하락" if pct < -FLAT_PCT else "보합"


def _summary(values: list[float]) -> dict:
    n = len(values)
    out = {"n": n, "up": sum(1 for v in values if direction(v) == "상승"),
           "down": sum(1 for v in values if direction(v) == "하락")}
    out["flat"] = n - out["up"] - out["down"]
    out["median"] = statistics.median(values) if n else None
    out["q1"], out["q3"] = (statistics.quantiles(values, n=4, method="inclusive")[::2] if n >= 2 else (None, None))
    return out


def compute_base_rate(bands: dict[str, list], price: dict[str, float], current: str, h: int, k: int = K_MAIN) -> dict:
    """비교기준비율(BaseRate) — 판정 가능 축이 k 개 이상인 과거 판정월 전부 중 결과가 확인된 달의 상승 비율·중위."""
    vals = [v for t in sorted(bands) if t < current and sum(b is not None for b in bands[t]) >= k
            if (v := outcome_pct(price, t, h)) is not None]
    return _summary(vals)


# ── 사례집 ─────────────────────────────────────────────────────────────────

@dataclass
class AnalogCasebook:
    today: str
    current: str
    current_bands: list
    chain: dict
    first_judgeable: str | None
    cases: dict[int, list[dict]]              # k → 사례 [{rep, members, outcomes{h: pct|None}}]
    stats: dict[int, dict[int, dict]]         # k → h → 대표월 요약
    member_stats: dict[int, dict]             # h → 주 k 의 묶인 달 전체 요약
    base: dict[int, dict]                     # h → 비교 기준
    loo: list[dict] = field(default_factory=list)   # 주 k·h=12 대표월 하나씩 뺀 결과


def build_analog_casebook(raw: dict, today: str) -> AnalogCasebook:
    axes_series, extra = prepare_inputs(raw)
    price = extra["price"]
    current = current_judge_month(axes_series)
    first = min(_m(min(s)) for s in axes_series.values())
    months = [_s(n) for n in range(first, _m(current) + 1)]
    bands = {t: judge_month_bands(axes_series, t) for t in months}
    if any(b is None for b in bands[current]):
        raise ValueError(f"현재 판정월 {current} 에 판정 불가 축이 있다")
    judgeable = [t for t in months if sum(b is not None for b in bands[t]) >= K_MAIN]
    cases, stats = {}, {}
    for k in (K_MAIN, *K_ALT):
        cs = group_nonoverlapping_cases(find_analog_months(bands, current, k))
        for c in cs:
            c["outcomes"] = {h: outcome_pct(price, c["rep"], h) for h in HORIZONS}
            c["bands"] = bands[c["rep"]]
        cases[k] = cs
        stats[k] = {h: _summary([c["outcomes"][h] for c in cs if c["outcomes"][h] is not None]) for h in HORIZONS}
    members = [t for c in cases[K_MAIN] for t in c["members"]]
    member_stats = {h: _summary([v for t in members if (v := outcome_pct(price, t, h)) is not None]) for h in HORIZONS}
    base = {h: compute_base_rate(bands, price, current, h) for h in HORIZONS}
    done = [c for c in cases[K_MAIN] if c["outcomes"][12] is not None]
    loo = [{"drop": c["rep"], **_summary([d["outcomes"][12] for d in done if d is not c])} for c in done]
    return AnalogCasebook(today, current, bands[current], extra["chain"], judgeable[0] if judgeable else None,
                          cases, stats, member_stats, base, loo)


# ── 문장·페이지 ─────────────────────────────────────────────────────────────

def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _ym(t: str) -> str:
    return f"{t[:4]}년 {int(t[5:7])}월"


def casebook_sentence(book: AnalogCasebook, k: int = K_MAIN, h: int = 12) -> str | None:
    """공개 문장 — 결과가 확인된 비중복 사례 < MIN_CASES 면 None(표만)."""
    s = book.stats[k][h]
    if s["n"] < MIN_CASES:
        return None
    return (f"미리 정한 6개 지표 구간 중 {k}개 이상이 지금과 같았던 비중복 사례 {s['n']}건(12개월 뒤 값이 확인된 것) 중 "
            f"{s['up']}건에서 서울 아파트 매매지수가 올랐습니다(내린 사례 {s['down']}건, 보합 {s['flat']}건). "
            f"사례끼리 겹치는 기간이 있어 서로 독립이 아닙니다.")


DISCLAIMER = ("공공 통계로 과거 사례를 정리한 참고 자료이며 매수·매도를 권하거나 앞으로의 가격을 말하지 않습니다. 과거 사례는 최신 "
              "개정 자료로 다시 분류한 것이라 당시 알려진 값과 다를 수 있습니다.")


def _band_cells(bands: list) -> str:
    return "".join(f"<td>{html.escape(b['band'])}<br><span class=mut>{b['value']:,.1f}</span></td>" if b else "<td>판정 불가</td>"
                   for b in bands)


def render_analogs_page(book: AnalogCasebook | None, today: str) -> str:
    """analogs.html — 현재 구간 → 문장(게이트) → 사례 전부 → 비교 기준 → 민감도 → 규칙·출처·고지."""
    from blog.build_site import BASE_URL, ga4_snippet
    from blog.daily_digest import _SITE_CSS
    head = (f"<!DOCTYPE html><html lang=ko><head><meta charset=utf-8><meta name=viewport content=\"width=device-width,initial-scale=1\">"
            f"<title>지금과 닮았던 과거 — 서울 아파트 닮은 국면 사례집</title>"
            f"<meta name=description content=\"금리·대출·유동성·공급·가격 흐름 6개 지표가 지금과 닮았던 과거 달들의 12·36개월 뒤 서울 아파트 매매지수 변화를 사례 전부로 보여 줍니다.\">"
            f"<link rel=canonical href=\"{BASE_URL}/analogs.html\"><style>{_SITE_CSS}"
            "table{width:100%;border-collapse:collapse;font-size:13px}td,th{border-bottom:1px solid #e3ded3;padding:6px 4px;text-align:left;vertical-align:top}"
            f"h2{{margin:22px 0 8px;font-size:17px}}</style>{ga4_snippet()}</head><body><div class=wrap>"
            "<nav class=top><a href=\"index.html\">구 허브</a><a href=\"macro.html\">거시 지표</a><a href=\"cycles.html\">금리 사이클</a>"
            "<a href=\"reach.html\">예산 계산기</a><a href=\"methodology.html\">방법론</a></nav>"
            "<div class=crumb><a href=\"index.html\">서울</a> › 닮은 국면 사례집</div><h1>지금과 닮았던 과거</h1>")
    if book is None:
        return head + f"<p class=meta>{today}</p><p>지표를 아직 모으지 못해 이번 판은 비어 있습니다.</p></div></body></html>"
    sentence = casebook_sentence(book)
    s12 = book.stats[K_MAIN][12]
    lead = sentence or (f"미리 정한 6개 지표 구간 중 {K_MAIN}개 이상이 지금과 같았던 비중복 사례 중 12개월 뒤 값이 확인된 것이 "
                        f"{s12['n']}건뿐이라 몇 건 중 몇 건으로 줄여 말하지 않고 사례만 아래에 적습니다.")
    cur_rows = "".join(
        f"<tr><td>{html.escape(a.label)}</td><td>{html.escape(b['band'])}</td><td>{b['value']:,.2f}{a.unit}</td>"
        f"<td>{b['q1']:,.2f} / {b['q2']:,.2f}</td><td>{_ym(b['obs'])}</td></tr>" for a, b in zip(AXES, book.current_bands))
    axis_head = "".join(f"<th>{html.escape(a.label.split('(')[0])}</th>" for a in AXES)
    case_rows = "".join(
        f"<tr><td>{_ym(c['rep'])}<br><span class=mut>묶인 달 {len(c['members'])}개</span></td>{_band_cells(c['bands'])}"
        + "".join(f"<td>{_pct(c['outcomes'][h])}" + (f" {direction(c['outcomes'][h])}" if c['outcomes'][h] is not None else " 미도래")
                  + "</td>" for h in HORIZONS) + "</tr>" for c in book.cases[K_MAIN])

    def stat_line(s: dict) -> str:
        if not s["n"]:
            return "결과가 확인된 달 없음"
        iqr = f", 가운데 절반 {_pct(s['q1'])}~{_pct(s['q3'])}" if s["q1"] is not None else ""
        return f"{s['n']}건 중 오름 {s['up']}·내림 {s['down']}·보합 {s['flat']}, 가운데 값 {_pct(s['median'])}{iqr}"

    cmp_rows = "".join(
        f"<tr><td>{h}개월 뒤</td><td>{stat_line(book.stats[K_MAIN][h])}</td><td>{stat_line(book.member_stats[h])}</td>"
        f"<td>{stat_line(book.base[h])}</td></tr>" for h in HORIZONS)
    sens_rows = "".join(f"<tr><td>{k}개 이상 같음</td><td>{len(book.cases[k])}건</td><td>{stat_line(book.stats[k][12])}</td></tr>"
                        for k in (K_ALT[0], K_MAIN, K_ALT[1]))
    loo_txt = " · ".join(f"{_ym(r['drop'])} 빼면 {r['n']}건 중 오름 {r['up']}" for r in book.loo) or "결과가 확인된 사례가 없어 생략"
    ch = book.chain
    rules = (f"규칙은 결과를 계산하기 전에 고정해 공개했습니다(PREREG {PREREG_VERSION}, 저장소 {PREREG_PATH}). 축마다 그 달까지 쌓인 값(60개 이상)을 "
             f"셋으로 나눠 낮음·중간·높음을 정하고, 판정월에는 공표 시차만큼 앞선 달의 값을 씁니다. 결과는 판정월에 알려진 마지막 지수 대비 "
             f"12·36개월 뒤 변화(명목, ±{FLAT_PCT}% 안은 보합)입니다. 같은 사례로 묶는 기준은 대표월 사이 {CASE_GAP_M}개월입니다. "
             f"가격지수는 한국부동산원 서울 아파트 매매지수 901Y093(2003-11~)에 901Y149 를 겹침 {ch['n_overlap']}개월"
             f"({ch['overlap_from']}~{ch['overlap_to']}) 평균 비율 {ch['factor']:.4f} 로 이은 작성자 연쇄이며 공식 장기지수가 아닙니다"
             f"(겹침 비율 폭 {ch['spread']:.2%}). 판정 가능한 첫 달은 {_ym(book.first_judgeable) if book.first_judgeable else '없음'}입니다.")
    body_text = " ".join([lead, DISCLAIMER, rules, loo_txt])
    assert_wording_ok(body_text, "analog_casebook:page")
    assert_lead_wording_ok(lead, "analog_casebook:lead")
    for w in _EXTRA_WORDS:
        if w in body_text:
            raise ValueError(f"analog_casebook: 금지 표현 '{w}'")
    return head + (
        f"<p class=meta>{today} · 판정월 {_ym(book.current)} · 한국은행 ECOS·한국부동산원</p>"
        f"<p class=lead>{html.escape(lead)}</p><p class=notice>{html.escape(DISCLAIMER)}</p>"
        f"<h2>지금의 지표 구간 <span class=mut>(그 달까지의 분포에서)</span></h2>"
        f"<div class=tblwrap><table><thead><tr><th>지표</th><th>구간</th><th>값</th><th>경계(낮음/높음)</th><th>관측월</th></tr></thead>"
        f"<tbody>{cur_rows}</tbody></table></div>"
        f"<h2>닮은 사례 전부 <span class=mut>({K_MAIN}개 이상 같음 · 대표월 기준)</span></h2>"
        f"<div class=tblwrap><table><thead><tr><th>대표월</th>{axis_head}<th>12개월 뒤</th><th>36개월 뒤</th></tr></thead>"
        f"<tbody>{case_rows or '<tr><td colspan=9>닮은 사례 없음</td></tr>'}</tbody></table></div>"
        f"<h2>견줘 볼 기준</h2><div class=tblwrap><table><thead><tr><th>지평</th><th>닮은 사례(대표월 1표)</th><th>닮은 달 전부</th>"
        f"<th>같은 기간 모든 달</th></tr></thead><tbody>{cmp_rows}</tbody></table></div>"
        f"<p class=mut>닮은 달 전부와 같은 기간 모든 달은 서로 겹치는 기간을 여러 번 셉니다.</p>"
        f"<h2>규칙을 바꾸면</h2><div class=tblwrap><table><thead><tr><th>닮음 기준</th><th>사례 수</th><th>12개월 뒤</th></tr></thead>"
        f"<tbody>{sens_rows}</tbody></table></div><p class=mut>사례 하나씩 빼고 다시 세면: {html.escape(loo_txt)}</p>"
        f"<h2>규칙과 출처</h2><p class=mut>{html.escape(rules)}</p>"
        f"<p class=mut>출처: 한국은행 경제통계시스템(ECOS) 121Y006·721Y001·161Y005·151Y003·901Y105, 한국부동산원 전국주택가격동향조사"
        f"(ECOS 901Y093·901Y149).</p>"
        f"<p class=foot><a href=\"methodology.html\">방법론 전문</a> · <a href=\"cycles.html\">기준금리 인상 사이클 뒤 지표 변화</a></p>"
        "</div></body></html>")
