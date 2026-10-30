"""닮은국면사례집(AnalogCasebook) — analogs.html. 지금과 지표 구간이 닮았던 과거 판정월의 12·36개월 뒤 서울 아파트 매매지수 변화를
"비중복 사례 전부"로 보인다. DIKW 검토 2026-10-30 P3(케이스2). **탐색적 사례집** — 예측력 검증이 아니다.

규칙 정본: `docs/analog-casebook-prereg-v2.md`(v1 결과를 본 뒤 고친 탐색적 수정, v2 결과 계산 전 커밋). v1 은 부록으로 함께 계산한다.
- 축 6개를 판정월 t 에 관측월 t−L(공표 시차)의 값으로, 그 축의 확장 창 3분위(유효값 60개 이상)로 낮음·중간·높음.
- 닮음규칙(MatchRule) 주 규칙(48, 4): 6축 모두 판정 가능, 현재와 48개월 이상 간격, A1 가격 흐름 구간·방향 일치 + 나머지 5축 중 4개 이상.
  변형 9개(간격 36·48·60 × 일치 3·4·5)를 0건 포함 전부 싣는다.
- 결과 = 기준 관측월 b=t−1 의 최신 개정 지수 대비 b+h(h=12 주·36 보조), ±0.5% 안은 보합. 끝 달이 아직 없으면 미도래, 있어야 할 값이 없으면 결측.
- 비중복 사례 = 대표월 사이 36개월 이상(독립 아님). 지평별 결과 확인 < 5 면 오름·내림 수·중위·사분위를 내지 않는다.
전망·인과·권유 없음, '확률' 단어 금지.
"""
from __future__ import annotations

import html
import statistics
from dataclasses import dataclass, field

from blog.wording_guard import assert_lead_wording_ok, assert_wording_ok

PREREG_VERSION = "v2"
PREREG_PATH = "docs/analog-casebook-prereg-v2.md"
PREREG_V1_PATH = "docs/analog-casebook-prereg-v1.md"
MIN_WINDOW = 60
HORIZONS = (12, 36)
FLAT_PCT = 0.5
CASE_GAP_M = 36
MIN_CASES = 5
CHAIN_MAX_SPREAD = 0.01
CHAIN_MIN_OVERLAP = 12
BANDS = ("낮음", "중간", "높음")
_EXTRA_WORDS = ("확률", "전망", "예상", "것이다", "때문에", "추천", "유망", "급등", "급락", "지금과 같은 시장", "아무 달")


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


@dataclass(frozen=True)
class MatchRule:
    """닮음규칙(MatchRule) — gap_m: 현재 판정월과의 최소 간격(개월). other_k: A1 구간·방향 필수 + 나머지 5축 일치 수(v2).
    total_k: 6축 대칭 투표 일치 수, 판정 가능 축 total_k 개 이상이면 후보(v1 부록)."""
    gap_m: int
    other_k: int | None = None
    total_k: int | None = None

    @property
    def label(self) -> str:
        if self.total_k is not None:
            return f"v1 · 6개 중 {self.total_k}개 이상 같음(가격 흐름 불일치 허용) · 지금 바로 앞 달 포함"
        return f"지금보다 {self.gap_m}개월 이상 앞 · 가격 흐름 같음 + 나머지 5개 중 {self.other_k}개 이상 같음"


RULE_V1 = MatchRule(gap_m=1, total_k=5)
RULE_MAIN = MatchRule(48, 4)
RULE_GRID = tuple(MatchRule(g, k) for g in (36, 48, 60) for k in (3, 4, 5))


def is_eligible(rule: MatchRule, bands_t: list) -> bool:
    if rule.total_k is not None:
        return sum(b is not None for b in bands_t) >= rule.total_k
    return all(b is not None for b in bands_t)


def is_analog(rule: MatchRule, bands_t: list, bands_c: list) -> bool:
    """판정 불가 축은 불일치. v2 는 A1 구간과 방향(원값 ±FLAT_PCT)이 모두 같아야 한다."""
    same = [b is not None and c is not None and b["band"] == c["band"] for b, c in zip(bands_t, bands_c)]
    if rule.total_k is not None:
        return sum(same) >= rule.total_k
    a1t, a1c = bands_t[0], bands_c[0]
    return (same[0] and direction(a1t["value"]) == direction(a1c["value"]) and sum(same[1:]) >= rule.other_k)


def candidate_months(bands: dict[str, list], current: str, rule: MatchRule) -> list[str]:
    """규칙의 후보 모집단 — 현재와 gap_m 개월 이상 앞서고 판정 가능 조건을 채운 판정월(비교 기준도 같은 모집단)."""
    return [t for t in sorted(bands) if _m(current) - _m(t) >= rule.gap_m and is_eligible(rule, bands[t])]


def find_analog_months(bands: dict[str, list], current: str, rule: MatchRule) -> list[str]:
    """닮은달(AnalogMonth) — 후보 모집단 중 규칙상 현재와 닮은 판정월."""
    return [t for t in candidate_months(bands, current, rule) if is_analog(rule, bands[t], bands[current])]


def group_nonoverlapping_cases(months: list[str]) -> list[dict]:
    """비중복사례(NonOverlappingCase) — 첫 닮은 달이 대표월, 대표월 + CASE_GAP_M 개월 안의 닮은 달은 그 사례에 묶는다."""
    cases: list[dict] = []
    for t in sorted(months):
        if cases and _m(t) < _m(cases[-1]["rep"]) + CASE_GAP_M:
            cases[-1]["members"].append(t)
        else:
            cases.append({"rep": t, "members": [t]})
    return cases


def outcome(price: dict[str, float], t: str, h: int) -> tuple[str, float | None]:
    """기준 관측월 b=t−1 의 최신 개정 지수 대비 b+h 변화율(%). ("값", pct) | ("미도래", None) | ("결측", None)."""
    b = shift_month(t, -1)
    end = shift_month(b, h)
    if end > max(price):
        return "미도래", None
    if b not in price or end not in price:
        return "결측", None
    return "값", (price[end] / price[b] - 1) * 100


def outcome_pct(price: dict[str, float], t: str, h: int) -> float | None:
    return outcome(price, t, h)[1]


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


def compute_base_rate(bands: dict[str, list], price: dict[str, float], current: str, h: int, rule: MatchRule) -> dict:
    """비교기준비율(BaseRate) — 규칙의 후보 모집단 중 결과가 확인된 모든 판정월의 오름·내림 수와 중위. 월별 결과 창은 겹친다."""
    months = candidate_months(bands, current, rule)
    out = _summary([v for t in months if (v := outcome_pct(price, t, h)) is not None])
    out["from"], out["to"] = (months[0], months[-1]) if months else (None, None)
    return out


# ── 사례집 ─────────────────────────────────────────────────────────────────

@dataclass
class Variant:
    rule: MatchRule
    cases: list[dict]                 # [{rep, members, bands, outcomes{h: (상태, pct)}}]
    stats: dict[int, dict]            # h → 대표월 1표 요약(결과 확인된 것만)
    base: dict[int, dict]             # h → 비교 기준


@dataclass
class AnalogCasebook:
    today: str
    current: str
    current_bands: list
    chain: dict
    first_judgeable: str | None       # 6축 모두 판정 가능한 첫 판정월
    main: Variant
    grid: list[Variant]
    v1: Variant
    loo: list[dict] = field(default_factory=list)   # 주 규칙·12개월, 결과 확인 ≥ MIN_CASES 일 때만


def _variant(rule: MatchRule, bands: dict, price: dict, current: str) -> Variant:
    cases = group_nonoverlapping_cases(find_analog_months(bands, current, rule))
    for c in cases:
        c["bands"] = bands[c["rep"]]
        c["outcomes"] = {h: outcome(price, c["rep"], h) for h in HORIZONS}
    stats = {h: _summary([c["outcomes"][h][1] for c in cases if c["outcomes"][h][0] == "값"]) for h in HORIZONS}
    return Variant(rule, cases, stats, {h: compute_base_rate(bands, price, current, h, rule) for h in HORIZONS})


def build_analog_casebook(raw: dict, today: str) -> AnalogCasebook:
    axes_series, extra = prepare_inputs(raw)
    price = extra["price"]
    current = current_judge_month(axes_series)
    first = min(_m(min(s)) for s in axes_series.values())
    months = [_s(n) for n in range(first, _m(current) + 1)]
    bands = {t: judge_month_bands(axes_series, t) for t in months}
    if any(b is None for b in bands[current]):
        raise ValueError(f"현재 판정월 {current} 에 판정 불가 축이 있다")
    full = [t for t in months if all(b is not None for b in bands[t])]
    main = _variant(RULE_MAIN, bands, price, current)
    grid = [main if r == RULE_MAIN else _variant(r, bands, price, current) for r in RULE_GRID]
    done = [c for c in main.cases if c["outcomes"][12][0] == "값"]
    loo = ([{"drop": c["rep"], **_summary([d["outcomes"][12][1] for d in done if d is not c])} for c in done]
           if len(done) >= MIN_CASES else [])
    return AnalogCasebook(today, current, bands[current], extra["chain"], full[0] if full else None, main, grid,
                          _variant(RULE_V1, bands, price, current), loo)


# ── 문장·페이지 ─────────────────────────────────────────────────────────────

def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _ym(t: str) -> str:
    return f"{t[:4]}년 {int(t[5:7])}월"


def _val(v: float, unit: str) -> str:
    return f"{v:,.0f}{unit}" if unit == "호" else f"{v:,.2f}{unit}"


def base_rate_sentence(book: AnalogCasebook, h: int = 12) -> str | None:
    """첫머리 비교 기준 문장(사례와 같은 모집단). 결과 확인 < MIN_CASES 면 None."""
    b = book.main.base[h]
    if b["n"] < MIN_CASES:
        return None
    return (f"{_ym(b['from'])}~{_ym(b['to'])} 사이 6개 지표가 모두 있는 달 {b['n']}개 가운데 {h}개월 뒤 서울 아파트 매매지수(명목)가 "
            f"{FLAT_PCT}% 넘게 오른 달은 {b['up']}개, {FLAT_PCT}% 넘게 내린 달은 {b['down']}개였습니다. 달마다 결과 기간이 겹쳐 같은 "
            f"상승기·하락기를 여러 번 셉니다. 이 집계는 지금 이후의 방향을 말하지 않습니다.")


def casebook_sentence(book: AnalogCasebook, h: int = 12) -> str:
    """닮은 사례 문장 — 결과 확인된 비중복 사례 < MIN_CASES 면 몇 건 중 몇 건으로 줄여 말하지 않는다."""
    v = book.main
    s = v.stats[h]
    rule = f"가격 흐름의 구간·방향이 같고 나머지 5개 지표 중 {v.rule.other_k}개 이상이 같은 달, 지금보다 {v.rule.gap_m}개월 이상 앞선 달"
    if not v.cases:
        return (f"그 가운데 미리 정한 규칙({rule})으로 지금과 닮은 달은 없었습니다. 규칙을 바꾼 변형 9개의 사례를 아래에 "
                f"0건까지 그대로 적습니다.")
    if s["n"] < MIN_CASES:
        return (f"그 가운데 미리 정한 규칙({rule})으로 지금과 닮은 비중복 사례는 {len(v.cases)}건이고 {h}개월 뒤 값이 확인된 것은 "
                f"{s['n']}건이라, 몇 건 중 몇 건으로 줄여 말하지 않고 사례만 아래에 적습니다.")
    return (f"그 가운데 미리 정한 규칙({rule})으로 지금과 닮은 비중복 사례 {s['n']}건({h}개월 뒤 값이 확인된 것) 중 {s['up']}건에서 "
            f"서울 아파트 매매지수가 올랐습니다(내린 사례 {s['down']}건, 보합 {s['flat']}건). 사례끼리 겹치는 기간이 있어 서로 독립이 아닙니다.")


DISCLAIMER = ("공공 통계로 과거 사례를 정리한 탐색적 참고 자료이며 매수·매도를 권하거나 앞으로의 가격을 말하지 않습니다. 과거 사례는 최신 "
              "개정 자료로 다시 분류한 것이라 당시 알려진 값과 다를 수 있습니다.")
_ERAS = (("2000년대 중반 상승기", "2007-12"), ("2008년 금융위기 뒤 하락기", "2009-12"), ("2010~2013년 침체기", "2013-12"))


def limits_text(book: AnalogCasebook) -> str:
    """한계 고지 — 비교 모집단 첫 달보다 앞선 국면은 빠졌다고 적는다(첫 달은 데이터에서 계산)."""
    first = book.first_judgeable
    missed = [name for name, end in _ERAS if first and end < first]
    head = (f"6개 지표가 모두 5년 치 이상 쌓인 {_ym(first)} 이후 달만 비교하므로 {', '.join(missed)}는 비교 대상에 들지 않습니다. "
            if missed else "")
    return (head + "6개 지표에는 정책 대출(예: 2023년 특례보금자리론)과 대출 규제 변화가 들어 있지 않습니다. "
            "규칙은 첫 계산(v1) 결과를 본 뒤 한 번 고쳤습니다(v2) — 예측력을 검증한 것이 아닙니다.")


def _outcome_txt(o: tuple[str, float | None]) -> str:
    state, pct = o
    return f"{_pct(pct)} {direction(pct)}" if state == "값" else state


def _cases_inline(v: Variant, h: int) -> str:
    s = v.stats[h]
    if s["n"] >= MIN_CASES:
        iqr = f", 가운데 절반 {_pct(s['q1'])}~{_pct(s['q3'])}" if s["q1"] is not None else ""
        return f"{s['n']}건 중 오름 {s['up']}·내림 {s['down']}·보합 {s['flat']}, 가운데 값 {_pct(s['median'])}{iqr}"
    if not v.cases:
        return "닮은 사례 없음"
    return " · ".join(f"{_ym(c['rep'])} {_outcome_txt(c['outcomes'][h])}" for c in v.cases)


def _case_table(v: Variant) -> str:
    head = "".join(f"<th>{html.escape(a.label.split('(')[0])}</th>" for a in AXES)
    rows = "".join(
        f"<tr><td>{_ym(c['rep'])}<br><span class=mut>묶인 달 {html.escape(', '.join(m[2:] for m in c['members']))}</span></td>"
        + "".join(f"<td>{html.escape(b['band'])}<br><span class=mut>{_val(b['value'], a.unit)}</span></td>" if b else "<td>판정 불가</td>"
                  for a, b in zip(AXES, c["bands"]))
        + "".join(f"<td>{_outcome_txt(c['outcomes'][h])}</td>" for h in HORIZONS) + "</tr>" for c in v.cases)
    return (f"<div class=tblwrap><table><thead><tr><th>대표월</th>{head}<th>12개월 뒤</th><th>36개월 뒤</th></tr></thead>"
            f"<tbody>{rows or '<tr><td colspan=9>닮은 사례 없음</td></tr>'}</tbody></table></div>")


def render_analogs_page(book: AnalogCasebook | None, today: str) -> str:
    """analogs.html — 비교 기준 → 닮은 사례 문장(게이트) → 지금 구간 → 사례 전부 → 변형 9개 → v1 부록 → 규칙·출처·고지."""
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
    base_txt = base_rate_sentence(book) or "비교할 수 있는 과거 달이 아직 적어 기준 집계를 싣지 않습니다."
    case_txt = casebook_sentence(book)
    cur_rows = "".join(
        f"<tr><td>{html.escape(a.label)}</td><td>{html.escape(b['band'])}</td><td>{_val(b['value'], a.unit)}</td>"
        f"<td>{_val(b['q1'], a.unit)} / {_val(b['q2'], a.unit)}</td><td>{_ym(b['obs'])}</td></tr>" for a, b in zip(AXES, book.current_bands))
    grid_rows = "".join(
        f"<tr><td>{v.rule.gap_m}개월</td><td>{v.rule.other_k}개</td><td>{len(v.cases)}건{' (주 규칙)' if v.rule == RULE_MAIN else ''}</td>"
        f"<td>{html.escape(_cases_inline(v, 12))}</td></tr>" for v in book.grid)
    base_rows = "".join(
        f"<tr><td>{h}개월 뒤</td><td>{b['n']}개 중 오름 {b['up']}·내림 {b['down']}·보합 {b['flat']}, 가운데 값 {_pct(b['median'])}"
        + (f", 가운데 절반 {_pct(b['q1'])}~{_pct(b['q3'])}" if b["q1"] is not None else "") + "</td></tr>"
        for h in HORIZONS if (b := book.main.base[h])["n"] >= MIN_CASES)
    loo_txt = (" · ".join(f"{_ym(r['drop'])} 빼면 {r['n']}건 중 오름 {r['up']}" for r in book.loo)
               if book.loo else "")
    ch = book.chain
    rules = (f"규칙은 v1 결과를 본 뒤 고친 v2 이며 v2 결과를 계산하기 전에 고정해 공개했습니다(저장소 {PREREG_PATH}, v1 은 {PREREG_V1_PATH}). "
             f"축마다 그 달까지 쌓인 값(60개 이상)을 셋으로 나눠 낮음·중간·높음을 정하고, 판정월에는 공표 시차만큼 앞선 달의 값을 씁니다. "
             f"결과는 규칙상 기준 관측월(판정월 직전 달)의 최신 개정 지수 대비 12·36개월 뒤 변화(명목, ±{FLAT_PCT}% 안은 보합)입니다. "
             f"같은 사례로 묶는 기준은 대표월 사이 {CASE_GAP_M}개월입니다. 가격지수는 한국부동산원 서울 아파트 매매지수 901Y093(2003-11~)에 "
             f"901Y149 를 이은 작성자 연쇄이며 공식 장기지수가 아닙니다 — 두 판이 겹치는 {ch['n_overlap']}개월({ch['overlap_from']}~"
             f"{ch['overlap_to']}) 동안 비율이 {ch['spread']:.2%} 안에서만 움직여 기준만 다른 같은 흐름으로 보고 평균 비율 {ch['factor']:.4f} 로 "
             f"{ch['joined_from']}부터 이었습니다. 6개 지표가 모두 판정 가능한 첫 달은 {_ym(book.first_judgeable) if book.first_judgeable else '없음'}입니다.")
    limits = limits_text(book)
    body_text = " ".join([base_txt, case_txt, DISCLAIMER, limits, rules, loo_txt])
    assert_wording_ok(body_text, "analog_casebook:page")
    assert_lead_wording_ok(base_txt + " " + case_txt, "analog_casebook:lead")
    for w in _EXTRA_WORDS:
        if w in body_text:
            raise ValueError(f"analog_casebook: 금지 표현 '{w}'")
    return head + (
        f"<p class=meta>{today} · 판정월 {_ym(book.current)} · 한국은행 ECOS·한국부동산원 · 탐색적 사례집</p>"
        f"<p class=lead>{html.escape(base_txt)}</p><p class=lead>{html.escape(case_txt)}</p>"
        f"<p class=notice>{html.escape(DISCLAIMER)}</p><p class=mut>{html.escape(limits)}</p>"
        f"<h2>지금의 지표 구간 <span class=mut>(그 달까지의 분포에서)</span></h2>"
        f"<div class=tblwrap><table><thead><tr><th>지표</th><th>구간</th><th>값</th><th>낮음 끝 / 중간 끝</th><th>관측월</th></tr></thead>"
        f"<tbody>{cur_rows}</tbody></table></div>"
        f"<h2>닮은 사례 전부 <span class=mut>(주 규칙 · 대표월 기준)</span></h2>{_case_table(book.main)}"
        + (f"<p class=mut>사례 하나씩 빼고 다시 세면: {html.escape(loo_txt)}</p>" if loo_txt else "")
        + (f"<h2>같은 기간 모든 달 <span class=mut>(월별 결과 기간이 겹침)</span></h2>"
           f"<div class=tblwrap><table><tbody>{base_rows}</tbody></table></div>" if base_rows else "")
        + f"<h2>규칙을 바꾸면 <span class=mut>(미리 정한 9개 변형 전부 · 12개월 뒤)</span></h2>"
        f"<div class=tblwrap><table><thead><tr><th>지금과의 간격</th><th>나머지 5개 중 같음</th><th>사례</th><th>12개월 뒤</th></tr></thead>"
        f"<tbody>{grid_rows}</tbody></table></div>"
        f"<p class=mut>결과가 확인된 사례가 {MIN_CASES}건 미만이면 몇 건 중 몇 건으로 묶지 않고 사례를 그대로 적습니다.</p>"
        f"<h2>부록 — 첫 계산(v1) 결과 <span class=mut>({html.escape(book.v1.rule.label)})</span></h2>{_case_table(book.v1)}"
        f"<p class=mut>v1 은 지금 바로 앞 달이 사례로 잡히고 가격 흐름이 반대인 달도 닮았다고 보는 문제가 있어 v2 로 고쳤습니다.</p>"
        f"<h2>규칙과 출처</h2><p class=mut>{html.escape(rules)}</p>"
        f"<p class=mut>출처: 한국은행 경제통계시스템(ECOS) 121Y006·721Y001·161Y005·151Y003·901Y105, 한국부동산원 전국주택가격동향조사"
        f"(ECOS 901Y093·901Y149).</p>"
        f"<p class=foot><a href=\"methodology.html\">방법론 전문</a> · <a href=\"cycles.html\">기준금리 인상 사이클 뒤 지표 변화</a></p>"
        "</div></body></html>")
