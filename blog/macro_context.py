"""거시 맥락(MacroContext) — L1 지표 카드 · 발표 직후 블록 · macro.html (2026-09-07 S2, 브리핑 §3.1).
입력 = agent_realestate.collectors.macro.collect_macro 스냅샷(report/blog/snapshots/macro/macro-{date}.json).
출력 = 사실 카드만: 값 · 직전 다른 값과 Δ · 같은 값이 시작된 날 · 1년 전 대비 Δ · 다음 발표일 · 출처 · 기준일.

문구 계약(A모델): 완성 텍스트는 assert_wording_ok + assert_lead_wording_ok(전망·인과·서수 패턴) 둘 다 통과해야 한다 —
build_macro_context 가 렌더 결과(태그 제거)에 대해 직접 호출한다. 수집 실패·오래된 지표(STALE_DAYS)는 카드가 없다
(지어내지 않음). 스냅샷이 없거나 MAX_SNAPSHOT_AGE_DAYS 보다 오래되면 컨텍스트 자체가 None → 스트립 생략.
시계열 한계: 네이버 경유 지표는 첫 수집 뒤 누적이라 초기엔 '1년 전 대비' 가 '—', 연속 횟수는 월별 관측 3개부터(보합·결측월에서 끊음).
"""
from __future__ import annotations

import math
import html
import re
from dataclasses import asdict, dataclass
from datetime import date, timedelta

from agent_realestate.collectors.macro import RELEASE_LABELS, next_release, recent_releases, upcoming_releases
from blog.macro_scenario import DEFAULT_LOAN_KRW, DEFAULT_RATE_PCT, DEFAULT_YEARS, build_scenario_table
from blog.wording_guard import assert_lead_wording_ok

MAX_SNAPSHOT_AGE_DAYS = 3
STALE_DAYS = {"daily": 7, "weekly": 14, "monthly": 100}     # event(기준금리 변경일)는 계단 시계열 — 오래됨 판정 없음
STRIP_CODES = ("bok_base", "cofix_new", "kr_govt3y", "fed_target_hi", "us10y", "usdkrw", "gold_krw_g")
TISTORY_STRIP_CODES = STRIP_CODES[:5]          # 티스토리는 인라인 스타일 비용이 커서 5카드(예산 30,000B 안 ≤3KB)
RELEASE_SHORT = {"bok": "금통위", "fomc": "FOMC", "cofix": "코픽스 공시"}
PAGE_CODES = STRIP_CODES + ("cofix_bal", "cd91", "us2y", "us_mortgage30", "kr_govt10y_m", "us_m2", "gold_usd_oz")
RELEASE_KIND_OF = {"bok_base": "bok", "fed_target_hi": "fomc", "cofix_new": "cofix", "cofix_bal": "cofix"}
RELEASE_CODE_OF = {"bok": "bok_base", "fomc": "fed_target_hi", "cofix": "cofix_new"}
MONTHS_12 = 365

# 전달시차(TransmissionLag) — 규정·공시 규약·이 사이트의 처리로 확인되는 구조적 지연만(출발, 도착, 지연, 근거).
# 은행채→고정금리 '수일~2주', 대출금리→매매계약 '1~3개월' 은 관측·규정 근거가 없어 싣지 않는다(S2 Codex).
TRANSMISSION_LAGS = (
    ("한국은행 기준금리 결정", "신규취급액 코픽스 공시", "다음 달 15일 15:00(주말·공휴일이면 다음 영업일)", "은행연합회 코픽스 공시 규약 — 전월 취급 자금조달비용 집계"),
    ("코픽스 공시", "신규 변동형 주담대 금리", "공시 다음 영업일부터 신규 취급분 적용", "은행연합회 코픽스 공시 안내 — 기존 대출은 약정별 금리 재산정 주기에 반영(신규와 다름)"),
    ("매매계약", "국토부 실거래 신고", "계약일부터 30일 이내", "부동산 거래신고 등에 관한 법률 제3조"),
    ("실거래 신고", "이 사이트의 12개월 중위", "즉시~수일", "매일 07:05 재수집 · 12개월 이동창"),
)
LAG_ONE_LINER = ("기준금리 결정 → 신규 코픽스(다음 달 15일 공시) → 신규 변동 주담대(공시 다음 영업일 적용, 기존 대출은 약정 재산정 주기) "
                 "→ 매매계약 → 실거래 신고(계약일부터 30일 이내) → 이 사이트의 12개월 중위")


@dataclass
class IndicatorCard:
    """지표카드 — 값·직전 Δ·같은 값 시작일·1년 전 Δ·연속 횟수·다음 발표·출처·기준일. 전부 관측 사실."""
    code: str
    label: str
    value_txt: str
    date: str
    delta_prev_txt: str
    since_txt: str
    delta_12m_txt: str
    streak_txt: str
    next_release_txt: str
    next_release_short: str
    source: str
    url: str


def _fmt_value(v: float, unit: str) -> str:
    if unit == "%":
        return f"{v:.2f}%"
    if unit == "원/g":
        return f"{v:,.0f}원/g"
    if unit == "$/oz":
        return f"${v:,.0f}/oz"
    if unit == "십억달러":
        return f"{v / 1000:,.2f}조달러"
    if unit == "원":
        return f"{v:,.1f}원"
    return f"{v:g}{unit}"


def _fmt_delta(cur: float, base: float | None, unit: str) -> str:
    if base in (None, 0):
        return "—"
    d = cur - base
    return f"{d:+.2f}%p" if unit == "%" else f"{d / base * 100:+.1f}%"


def value_on(series: list, kind: str, target: date, tolerance_days: int = 45) -> float | None:
    """target 시점 값 — changes 는 그날 이하 마지막 변경값(계단), observations 는 그날 이하 가장 가까운 관측(허용 창 안)."""
    best = None
    for d, v in series:
        dd = date.fromisoformat(d)
        if dd <= target:
            best = (dd, v)
        else:
            break
    if best is None:
        return None
    if kind != "changes" and (target - best[0]).days > tolerance_days:
        return None
    return best[1]


def _streak_txt(ind: dict) -> str:
    """월 단위 관측의 인접 차이가 같은 부호로 이어진 횟수(2회 이상만). 보합(차이 0)·결측월(달이 건너뜀)에서 끊는다(S2 Codex)."""
    if ind.get("freq") != "monthly":
        return ""
    pts = [(date.fromisoformat(d), v) for d, v in ind["series"]]
    if len(pts) < 3:
        return ""
    n, sign, i = 0, 0, len(pts) - 1
    while i > 0:
        (d0, v0), (d1, v1) = pts[i - 1], pts[i]
        if (d1.year * 12 + d1.month) - (d0.year * 12 + d0.month) != 1:
            break
        s = 1 if v1 > v0 else -1 if v1 < v0 else 0
        if s == 0 or (sign and s != sign):
            break
        sign, n, i = s, n + 1, i - 1
    return f"{n}회 연속 {'상승' if sign > 0 else '하락'}(월별 관측)" if n >= 2 else ""


def _is_stale(ind: dict, today: date) -> bool:
    limit = STALE_DAYS.get(ind.get("freq"))
    d = validate_iso_date(ind.get("date"))                        # 거부된 날짜를 다시 파싱하지 않는다(S6e Codex E1)
    return limit is not None and d is not None and (today - date.fromisoformat(d)).days > limit


def _numeric(v) -> bool:
    """카드·발표 블록에 쓸 수 있는 값인지 — None·''·문자열·NaN 은 수집 실패로 본다(S6c Codex R2: 손상 스냅샷이 월간 거시 절을 통째로 떨어뜨리지 않게)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def validate_iso_date(v) -> str | None:
    """달력형 ISO 날짜 'YYYY-MM-DD' 문자열일 때만 그대로 — None·''·형식 오류·기본형(20260914)·주차형(2026-W38-1)·시각 포함은 None
    (S6d/S6e Codex R2·E2: 'None'·빈 날짜 게재 방지 + 발표일 전후의 문자열 비교는 같은 표기에서만 성립)."""
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return None
    try:
        date.fromisoformat(v)
    except ValueError:
        return None
    return v


def _usable(ind: dict | None, today: date) -> bool:
    """카드로 쓸 수 있는 지표 — 있고, 값이 숫자이고, 기준일이 날짜이고, 오래되지 않았다(아니면 수집 실패처럼 빠진다)."""
    return bool(ind) and _numeric(ind.get("value")) and validate_iso_date(ind.get("date")) is not None and not _is_stale(ind, today)


def _fresh_pair(other: dict | None, ind: dict, today: date) -> dict | None:
    """보조 지표(연준 하단 등)는 본 지표와 같은 기준일이고 오래되지 않았을 때만 함께 쓴다(S2 Codex: 하단만 오래돼 4.25~3.75% 같은 범위 방지)."""
    return other if _usable(other, today) and other.get("date") == ind["date"] else None


def _card(ind: dict, all_ind: dict, today: date) -> IndicatorCard:
    unit, kind = ind["unit"], ind.get("series_kind", "observations")
    value_txt = _fmt_value(ind["value"], unit)
    label = ind["label"]
    lo = _fresh_pair(all_ind.get("fed_target_lo"), ind, today)
    if ind["code"] == "fed_target_hi" and lo:
        label, value_txt = "미 연방기금 목표범위", f"{lo['value']:.2f}~{ind['value']:.2f}%"
    prev = ind.get("prev_value")
    delta_prev_txt = f"{_fmt_delta(ind['value'], prev, unit)}(직전 {_fmt_value(prev, unit)})" if _numeric(prev) else "—"
    since = validate_iso_date(ind.get("since"))
    if kind == "changes":
        since_txt = f"{ind['date']} 변경"
    elif ind.get("since_window_start") or since is None or since == ind["date"]:
        since_txt = ""                                            # 보존 창 시작까지 같은 값·시작일 결측 → 시작일을 단정하지 않는다(S1 Codex·S6d R2)
    else:
        since_txt = f"{since} 이후 같은 값"
    ref = today if kind == "changes" else date.fromisoformat(ind["date"])   # 관측 지표는 기준일의 1년 전(지연 발표 월별 지표 — S2 Codex)
    v12 = value_on([(d, v) for d, v in ind["series"]], kind, ref - timedelta(days=MONTHS_12))
    delta_12m_txt = f"{_fmt_delta(ind['value'], v12, unit)}(1년 전 {_fmt_value(v12, unit)})" if v12 is not None else "—"
    rk = RELEASE_KIND_OF.get(ind["code"])
    nr = next_release(rk, today) if rk else None
    next_txt = f"{nr.isoformat()} {RELEASE_LABELS[rk]}" if nr else ("일정 미등록" if rk else "")
    next_short = f"{nr.strftime('%m-%d')} {RELEASE_SHORT[rk]}" if nr else ("미등록" if rk else "")
    return IndicatorCard(ind["code"], label, value_txt, ind["date"], delta_prev_txt, since_txt, delta_12m_txt,
                         _streak_txt(ind), next_txt, next_short, ind["source"], ind["url"])


def build_indicator_cards(snapshot: dict, today: date) -> list[IndicatorCard]:
    """PAGE_CODES 순서로 카드 — 수집 실패·오래된 지표는 빠진다."""
    ind = snapshot.get("indicators") or {}
    return [_card(ind[c], ind, today) for c in PAGE_CODES if _usable(ind.get(c), today)]


def build_release_block(snapshot: dict, today: date, window_days: int = 2) -> dict | None:
    """발표 직후 블록 — 최근 window 일 안의 발표마다 수집된 값(또는 '미갱신')과 다음 단계 시차 한 줄."""
    rel = recent_releases(today, window_days)
    if not rel:
        return None
    ind = snapshot.get("indicators") or {}
    lines = []
    for r in rel:
        x = ind.get(RELEASE_CODE_OF[r["kind"]])
        head = f"{r['label']}({r['date']})"
        if not _usable(x, today):                              # 카드와 같은 신선도·숫자 규칙(S2/S6c Codex)
            lines.append(f"{head}: 지표 미수집" + ("(오래됨)" if x and _is_stale(x, today) else ""))
            continue
        unit, pv = x["unit"], x.get("prev_value")
        prev_txt = f"직전 {_fmt_value(pv, unit)}, {_fmt_delta(x['value'], pv, unit)}" if _numeric(pv) else "직전값 미확인"
        since_ok = not x.get("since_window_start") and validate_iso_date(x.get("since")) is not None   # 보존 창 시작까지 같은 값·시작일 결측이면 단정하지 않는다
        if r["kind"] == "bok":
            if x["date"] >= r["date"]:
                body = f"기준금리 {_fmt_value(x['value'], unit)}({prev_txt})"
            else:
                body = f"기준금리 표에 새 변경 행 없음 — {_fmt_value(x['value'], unit)} 유지 또는 bok.or.kr 미갱신"
            nc = next_release("cofix", today)
            tail = f"다음 코픽스 공시 {nc.isoformat()} · 신규 변동 주담대는 공시 다음 영업일부터 적용(기존 대출은 약정 재산정 주기)" if nc else ""
            tail += f" · {_bp25_line()}"
        elif x["date"] < r["date"]:                              # 발표 뒤 관측이 아직 없음 — 이전 값을 당일 발표값처럼 쓰지 않는다(S3 Codex P1)
            body = f"발표 후 관측 없음(마지막 관측 {x['date']} {_fmt_value(x['value'], unit)})"
            tail = ""
        elif r["kind"] == "fomc":
            lo = _fresh_pair(ind.get("fed_target_lo"), x, today)
            rng = f"{lo['value']:.2f}~{x['value']:.2f}%" if lo else f"상단 {_fmt_value(x['value'], unit)}"
            body = f"목표범위 {rng}(관측 {x['date']}" + (f", 같은 값 시작 {x['since']})" if since_ok else ")")
            us10 = ind.get("us10y")
            tail = f"미 국채 10년 {_fmt_value(us10['value'], '%')}({us10['date']})" if _usable(us10, today) else ""
        else:
            parts = ([prev_txt] if _numeric(pv) else []) + ([f"반영 {x['since']}"] if since_ok else [])
            body = f"신규취급액 {_fmt_value(x['value'], unit)}" + (f"({', '.join(parts)})" if parts else "")
            tail = "신규 변동형 주담대 금리는 공시 다음 영업일부터 적용(기존 대출은 약정별 재산정 주기)"
        lines.append(f"{head}: {body}" + (f" · {tail}" if tail else ""))
    return {"releases": rel, "lines": lines}


def _bp25_line() -> str:
    """대출금리 25bp 변화의 월 상환 산수(기본 가정 대출) — 전달 폭은 말하지 않는다."""
    d = build_scenario_table(DEFAULT_LOAN_KRW, DEFAULT_YEARS, DEFAULT_RATE_PCT, (0, 25))[1]["delta_monthly_krw"]
    return (f"대출금리 25bp 변화(기준 {DEFAULT_RATE_PCT:.2f}% [가정] · {DEFAULT_LOAN_KRW / 1e8:g}억·{DEFAULT_YEARS}년 원리금균등) "
            f"= 월 상환 약 ±{d:,}원(산수, 전달 폭은 별도)")


def _plain(h: str) -> str:
    return re.sub(r"<[^>]+>", " ", h)


def build_macro_context(snapshot: dict | None, today: str) -> dict | None:
    """스냅샷 → {asof, cards, release_block, upcoming, lags, tistory_html, site_html, page_html}. 없으면 None."""
    if not snapshot or not snapshot.get("asof"):
        return None
    t = date.fromisoformat(today)
    if (t - date.fromisoformat(snapshot["asof"])).days > MAX_SNAPSHOT_AGE_DAYS:
        return None
    cards = build_indicator_cards(snapshot, t)
    if not cards:
        return None
    ctx = {"asof": snapshot["asof"], "today": today, "cards": [asdict(c) for c in cards],
           "release_block": build_release_block(snapshot, t), "upcoming": upcoming_releases(t),
           "lags": TRANSMISSION_LAGS, "errors": snapshot.get("errors") or {}}
    ctx["tistory_html"] = render_macro_strip_tistory(ctx)
    ctx["site_html"] = render_macro_strip_site(ctx)
    ctx["page_html"] = render_macro_page(ctx, today)
    for k in ("tistory_html", "site_html", "page_html"):
        assert_lead_wording_ok(_plain(ctx[k]), f"macro:{k}")
    return ctx


# ── 렌더 ────────────────────────────────────────────────────────────────────────
def _strip_cards(ctx: dict, codes: tuple = STRIP_CODES) -> list[dict]:
    by = {c["code"]: c for c in ctx["cards"]}
    return [by[c] for c in codes if c in by]


def _ga(code: str) -> str:
    return f"onclick=\"if(typeof gtag==='function')gtag('event','macro_click',{{code:'{code}'}})\""


def render_macro_strip_tistory(ctx: dict) -> str:
    """티스토리용(인라인 스타일, p/b/br/a/span/table/tr/td 만) — 스트립 카드 + 발표 직후 + 시차 한 줄."""
    from blog.build_site import BASE_URL
    from blog.daily_digest import _MUT, _TBL, _TH
    rows = "".join(                                             # 셀 인라인 스타일 생략(티스토리 테마 기본값) — 예산 절약
        f'<tr><td><a href="{html.escape(c["url"])}">{c["label"]}</a> <span style="{_MUT}">{c["date"][5:]}</span></td>'
        f'<td><b>{c["value_txt"]}</b></td><td>{c["delta_prev_txt"]}</td>'
        f'<td>{c["delta_12m_txt"].split("(")[0]}</td><td>{c["next_release_short"] or "—"}</td></tr>'
        for c in _strip_cards(ctx, TISTORY_STRIP_CODES))
    rel = ctx["release_block"]
    rel_p = f'<p><b>발표 직후</b> — {" / ".join(html.escape(x) for x in rel["lines"])}</p>' if rel else ""
    return (f'<p><b>거시 지표</b> <span style="{_MUT}">(기준일별 관측 사실 · 출처 링크 · 자체 해석 없음)</span></p>{rel_p}'
            f'<table style="{_TBL}"><tr><td style="{_TH}"><b>지표(기준일)</b></td><td style="{_TH}"><b>값</b></td>'
            f'<td style="{_TH}"><b>직전 대비</b></td><td style="{_TH}"><b>1년 전</b></td><td style="{_TH}"><b>다음 발표</b></td></tr>{rows}</table>'
            f'<p style="{_MUT}">시차(구조): {LAG_ONE_LINER} · 환율·금·캘린더·전체 지표: <a href="{BASE_URL}/macro.html">거시 지표 페이지</a></p>')


def render_macro_strip_site(ctx: dict) -> str:
    """사이트 일간 페이지용(클래스 CSS) — GA4 macro_click 이벤트 배선(K10 분자)."""
    rows = "".join(
        f'<tr><td><a href="{html.escape(c["url"])}" {_ga(c["code"])}>{c["label"]}</a><br><span class=mut>{c["date"]}</span></td>'
        f'<td><b>{c["value_txt"]}</b></td><td>{c["delta_prev_txt"]}</td><td>{c["delta_12m_txt"]}</td><td>{c["next_release_txt"] or "—"}</td></tr>'
        for c in _strip_cards(ctx))
    rel = ctx["release_block"]
    rel_p = f'<p class=rel><b>발표 직후</b> — {" / ".join(html.escape(x) for x in rel["lines"])}</p>' if rel else ""
    return (f'<h2>거시 지표 <span class=mut>(기준일별 관측 사실 · 자체 해석 없음)</span></h2>{rel_p}'
            f'<div class=tblwrap><table><tr><th>지표(기준일)</th><th>값</th><th>직전 대비</th><th>1년 전 대비</th><th>다음 발표</th></tr>{rows}</table></div>'
            f'<p class=mut>시차(구조): {LAG_ONE_LINER} · <a href="../macro.html" {_ga("page")}>캘린더·전체 지표 →</a></p>')


def render_macro_page(ctx: dict | None, today: str) -> str:
    """macro.html — 발표 직후 · 지표 카드 전체 · 발표 캘린더(45일) · 전달시차 표 · 방법론. ctx 없으면 '미수집' 페이지."""
    from blog import build_explorer as be
    from blog.build_site import BASE_URL, ga4_snippet
    from blog.daily_digest import _SITE_CSS
    lag_rows = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td><td class=mut>{d}</td></tr>" for a, b, c, d in TRANSMISSION_LAGS)
    if ctx is None:
        body = (f'<p class=meta>{today} 기준 — 거시 지표 <b>미수집</b>(수집 실패 또는 스냅샷 {MAX_SNAPSHOT_AGE_DAYS}일 초과). '
                f'카드를 만들지 않는다(지어내지 않음).</p>')
        cards_html, cal_html, rel_html = "", "", ""
    else:
        body = (f'<p class=meta>수집 기준일 {ctx["asof"]} · 지표 {len(ctx["cards"])}개 · 관측 사실만(자체 점수·해석 없음) · '
                f'<a href="calc.html" {_ga("calc")}>전달 계산기 →</a> · <a href="cycles.html" {_ga("cycles")}>과거 인상사이클 →</a></p>')
        rel = ctx["release_block"]
        rel_html = (f'<h2>발표 직후</h2><ul>{"".join(f"<li>{html.escape(x)}</li>" for x in rel["lines"])}</ul>') if rel else ""
        cards_html = ('<h2>지표 카드</h2><div class=tblwrap><table><tr><th>지표</th><th>값(기준일)</th><th>직전 대비</th>'
                      '<th>같은 값 시작</th><th>1년 전 대비</th><th>연속</th><th>다음 발표</th><th>출처</th></tr>' + "".join(
            f'<tr><td>{c["label"]}</td><td><b>{c["value_txt"]}</b><br><span class=mut>{c["date"]}</span></td>'
            f'<td>{c["delta_prev_txt"]}</td><td>{c["since_txt"] or "—"}</td><td>{c["delta_12m_txt"]}</td><td>{c["streak_txt"] or "—"}</td>'
            f'<td>{c["next_release_txt"] or "—"}</td><td><a href="{html.escape(c["url"])}" {_ga(c["code"])}>{c["source"]}</a></td></tr>'
            for c in ctx["cards"]) + "</table></div>")
        cal_rows = "".join(f'<tr><td>{u["date"]}</td><td>{u["label"]}</td></tr>' for u in ctx["upcoming"])
        cal_html = (f'<h2>발표 캘린더 <span class=mut>(45일)</span></h2><div class=tblwrap><table><tr><th>날짜</th><th>발표</th></tr>{cal_rows}</table></div>'
                    if cal_rows else '<h2>발표 캘린더</h2><p class=mut>45일 안에 등록된 발표 일정 없음(연 1회 갱신).</p>')
    return f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>거시 지표 — 서울 부동산 데이터 스냅샷</title>
<meta name=description content="한국은행 기준금리·코픽스·국고채·미 연준 목표범위·미 국채·환율·금의 기준일별 관측값, 발표 캘린더, 대출·실거래까지의 구조적 시차. 자체 해석 없음.">
<style>{_SITE_CSS}</style>
{ga4_snippet()}
</head><body>
<div class=wrap>
<nav class=top><a href="index.html">구 허브</a><a href="explorer.html">탐색기</a><a href="daily/latest.html">오늘의 변화</a><a href="methodology.html">방법론</a></nav>
<div class=crumb><a href="index.html">서울</a> › 거시 지표</div>
<h1>거시 지표</h1>
{body}
{rel_html}
{cards_html}
{cal_html}
<h2>전달시차 <span class=mut>(구조적 지연의 사실)</span></h2>
<div class=tblwrap><table><tr><th>출발</th><th>도착</th><th>지연</th><th>근거</th></tr>{lag_rows}</table></div>
<div class=foot>
값은 각 출처의 공표 관측값이며 이 사이트는 해석을 붙이지 않는다. '직전 대비'는 직전 다른 값과의 차이, '1년 전 대비'는 계단 지표(기준금리)는 오늘의 365일 전 유효값, 관측 지표는 기준일의 365일 전(45일 안 가장 가까운 관측)과의 차이.
코픽스는 은행연합회 공시(매월 15일 15:00, 주말·공휴일이면 다음 영업일)를 네이버금융 경유로 읽는다. FOMC 는 한국시간 발표일(미국 회의 2일차 익일 새벽)로 적는다. 발표 캘린더는 연 1회 갱신하며 미등록 연도는 '일정 미등록' 으로 표기한다.<br>
{be.DISCLAIMER} {be._takedown()}<br>
<a href="methodology.html">방법론 전문</a> · <a href="daily/latest.html">오늘의 변화</a> · 코드: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a> · <a href="{BASE_URL}/macro.html">{BASE_URL}/macro.html</a>
</div>
</div>
</body></html>"""
