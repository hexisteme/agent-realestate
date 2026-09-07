"""사이클리포트(CycleReport, 2026-09-07 MacroContext S5) — L3 조건부 과거 통계.
기준금리 변경 시계열에서 인상사이클(HikeCycle)을 기계적으로 자르고, 사이클 시작 후 h개월의 지표 변화를 "n회 중 m회" 로 센다
(조건부통계 ConditionalStat). 전망·인과 문장 없음. 완료 사이클 < MIN_CYCLES 또는 Δ 부호 불일치 → "일관된 패턴 없음".
주의: 발행 문구에 '부호가' 를 쓰면 금칙어 '호가' 로 잡힌다 — '부호 전부/부호 일치' 로 쓴다.
서울 실거래 중위·거래량·코픽스의 과거 시계열은 미보유(MOLIT 2006~ 백필·ECOS 키 전) → '자료 없음' 으로 적고 지어내지 않는다.
국면 라벨(analysts/regime.py 의 OVERHEATED 등)은 발행하지 않는다. 첫 발행 전 사람 검토(계획 게이트 ④) — 그 전엔 site 만,
티스토리 원고 제외(period_delta.MACRO_SECTION_TISTORY)."""
from __future__ import annotations

import math
import calendar
import html
import statistics as st
from datetime import date

from blog.macro_context import LAG_ONE_LINER, MAX_SNAPSHOT_AGE_DAYS, _ga, _plain, build_indicator_cards, value_on, validate_iso_date
from blog.wording_guard import assert_lead_wording_ok

HORIZONS_M = (6, 12)
MIN_CYCLES = 3
CYCLE_TARGETS = (("kr_govt10y_m", "국고채 10년(월평균)"),)     # 사이클 시작 후 변화를 셀 지표 — 장기 시계열을 보유한 것만
MISSING_TARGETS = (("서울 아파트 실거래 12개월 중위", "MOLIT 2006~ 월중위 백필 전 — 자료 없음"),
                   ("서울 아파트 월 거래량", "같은 백필 전 — 자료 없음"),
                   ("신규 코픽스", "관측 창이 2026-06 이후뿐(네이버금융 일별 표) — 사이클 대조 자료 없음"))
SECTION_CODES = ("bok_base", "cofix_new", "kr_govt10y_m", "fed_target_hi")   # 월간 '거시 맥락' 절의 관측 줄


def _add_months(d: date, n: int) -> date:
    """n개월 뒤 같은 날(그 달에 없는 날이면 말일) — 28일 고정 클램프는 목표일을 앞당겨 관측 선택을 바꾼다(S5 Codex)."""
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, min(d.day, calendar.monthrange(d.year + y, m + 1)[1]))


def detect_hike_cycles(changes: list) -> list[dict]:
    """기준금리 변경 시계열([[날짜, 값], ...] — 변경일만) → 인상사이클 목록(시간순).
    사이클 = 인하(또는 시계열 시작) 뒤 첫 인상부터 다음 인하 전 마지막 인상까지. 마지막 변경이 인상이면 ongoing=True."""
    ser = sorted((str(d), float(v)) for d, v in changes)
    cycles: list[dict] = []
    cur: dict | None = None
    for (_, v0), (d1, v1) in zip(ser, ser[1:]):
        if v1 > v0:
            if cur is None:
                cur = {"start": d1, "from_pct": v0, "first_pct": v1, "end": d1, "peak_pct": v1, "n_hikes": 0, "total_bp": 0, "ongoing": True}
            cur.update(end=d1, peak_pct=v1, n_hikes=cur["n_hikes"] + 1, total_bp=cur["total_bp"] + round((v1 - v0) * 100))
        elif v1 < v0 and cur is not None:
            cur["ongoing"] = False
            cycles.append(cur)
            cur = None
    if cur is not None:
        cycles.append(cur)
    for c in cycles:
        s, e = date.fromisoformat(c["start"]), date.fromisoformat(c["end"])
        c["months"] = (e.year - s.year) * 12 + e.month - s.month
    return cycles


def compute_conditional_stats(events: list[str], series: list, horizons: tuple = HORIZONS_M, kind: str = "observations",
                              asof: str | None = None) -> dict:
    """events(사이클 시작일)마다 시작 시점 값과 h개월 뒤 값의 차이 → horizon 별
    {n, n_up, n_down, n_flat, median_delta, rows, bases, pending, missing, no_base, consistent}.
    시작 값 없음(시계열 이전) → no_base · 목표일이 asof(수집 기준일, 없으면 마지막 관측일) 뒤 → pending(미도래) ·
    목표일은 지났는데 허용 창(45일) 안 관측이 없음 → missing(관측 없음). 셋 다 n 에 안 센다. consistent = n ≥ MIN_CYCLES 이고 Δ 부호 전부 같음.
    값 조회는 macro_context.value_on(관측 지표: 그날 이하 가장 가까운 관측, 45일 안 · 계단 지표: 그날 이하 마지막 값)과 동일 규칙 —
    계단 지표는 미래 날짜에도 값을 돌려주므로 asof 게이트가 먼저다(S5 Codex P1)."""
    ser = sorted((str(d), float(v)) for d, v in series)
    limit = date.fromisoformat(asof) if asof else (date.fromisoformat(ser[-1][0]) if ser else date.min)
    out: dict[int, dict] = {}
    for h in horizons:
        rows, bases, pending, missing, no_base = [], {}, [], [], []
        for ev in events:
            d0 = date.fromisoformat(ev)
            base = value_on(ser, kind, d0)
            if base is None:
                no_base.append(ev)
                continue
            bases[ev] = base
            target = _add_months(d0, h)
            if target > limit:
                pending.append(ev)
                continue
            later = value_on(ser, kind, target)
            if later is None:
                missing.append(ev)
                continue
            rows.append({"event": ev, "base": base, "later": later, "delta": round(later - base, 3)})
        n = len(rows)
        n_up, n_down = sum(r["delta"] > 0 for r in rows), sum(r["delta"] < 0 for r in rows)
        out[h] = {"n": n, "n_up": n_up, "n_down": n_down, "n_flat": n - n_up - n_down,
                  "median_delta": round(st.median(r["delta"] for r in rows), 3) if rows else None,
                  "rows": rows, "bases": bases, "pending": pending, "missing": missing, "no_base": no_base,
                  "consistent": n >= MIN_CYCLES and (n_up == n or n_down == n)}
    return out


def stat_txt(s: dict, unit: str = "%p") -> str:
    """조건부통계 한 줄 — 브리핑 §4 계약: 완료 사이클 < MIN_CYCLES 또는 Δ 부호 불일치면 표·횟수·중위 대신 판정 한 줄만.
    부호 일치일 때만 'n회 중 n회 상승/하락, 중위 Δ' 를 적는다."""
    if s["n"] == 0:
        return "대조 가능한 완료 사이클 없음"
    if s["n"] < MIN_CYCLES:
        return f'완료 사이클 {s["n"]}회({MIN_CYCLES}회 미만) — 통계 없음'
    if not s["consistent"]:
        return f'일관된 패턴 없음(완료 사이클 {s["n"]}회 대조)'
    return f'{s["n"]}회 중 {s["n"]}회 {"상승" if s["n_up"] == s["n"] else "하락"}, 중위 Δ {s["median_delta"]:+.2f}{unit} — 부호 일치'


def _num(v) -> float | None:
    """스냅샷 값이 숫자일 때만 float — None·''·문자열·NaN 은 None(손상 스냅샷도 절 생략이 아니라 폴백, S6c Codex R2)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def build_cycle_report(snapshot: dict | None, today: str) -> dict | None:
    """macro 스냅샷의 bok_base 변경 시계열로 인상사이클 표 + 대상 지표별 조건부통계. bok_base 없으면 None(페이지는 '미수집')."""
    ind = (snapshot or {}).get("indicators") or {}
    bok = ind.get("bok_base")
    if not bok or bok.get("series_kind") != "changes" or not bok.get("series"):
        return None
    cycles = detect_hike_cycles(bok["series"])
    events = [c["start"] for c in cycles if not c["ongoing"]]       # 완료 사이클만 표본 — '완료 n회 대조' 문구와 MIN_CYCLES 기준이 같은 집합(S6 Codex F2)
    last_change = max((str(d), float(v)) for d, v in bok["series"])
    bok_last = validate_iso_date(bok.get("date")) or last_change[0]  # date/value/asof 결측·None·빈값·형식 오류 전부 시계열 마지막 변경으로 폴백(S6 F7·S6b N2·S6d R2)
    bok_value = _num(bok.get("value"))
    if bok_value is None:                                            # None·''·문자열 전부 폴백(S6c Codex R2) — 카드 경로는 macro_context._usable 이 같은 값을 거른다
        bok_value = last_change[1]
    asof = snapshot.get("asof") or bok_last
    targets = []
    for code, label in CYCLE_TARGETS:
        t = ind.get(code)
        if not t or not t.get("series"):
            targets.append({"code": code, "label": label, "missing": "이번 스냅샷에 없음(수집 실패)"})
            continue
        targets.append({"code": code, "label": label, "unit": t.get("unit") or "", "first": t["series"][0][0], "last": t["series"][-1][0],
                        "source": t.get("source") or "", "url": t.get("url") or "",
                        "stats": compute_conditional_stats(events, t["series"], HORIZONS_M, t.get("series_kind", "observations"),
                                                           asof=snapshot.get("asof"))})   # None 이면 함수가 대상 시계열 마지막 관측일로 폴백
    n_done = sum(1 for c in cycles if not c["ongoing"])
    return {"asof": asof, "today": today, "cycles": cycles, "n_completed": n_done, "enough": n_done >= MIN_CYCLES,
            "bok_first": min(str(d) for d, _ in bok["series"]), "bok_last": bok_last, "bok_value": bok_value, "targets": targets,
            "missing": MISSING_TARGETS}


def _delta_unit(unit: str) -> str:
    return "%p" if unit == "%" else unit


def _target_html(t: dict, cycles: list[dict]) -> str:
    """대상 지표 블록 — 판정 줄은 horizon 마다 항상, 사이클별 반응 표는 '부호 일치' horizon 이 있을 때만 그 열로(브리핑 §4 계약)."""
    if t.get("missing"):
        return f'<h3>{t["label"]}</h3><p class=mut>{t["missing"]}</p>'
    du = _delta_unit(t["unit"])
    hs = [h for h, s in t["stats"].items() if s["consistent"]]
    summ = "".join(f'<li>+{h}개월: {html.escape(stat_txt(s, du))}</li>' for h, s in t["stats"].items())
    table = ""
    if hs:
        head = "".join(f"<th>+{h}개월 값</th><th>Δ</th>" for h in hs)
        trs = []
        for c in cycles:
            cells = ""
            for h in hs:
                s = t["stats"][h]
                r = next((x for x in s["rows"] if x["event"] == c["start"]), None)
                if c["ongoing"]:
                    cells += '<td colspan=2 class=mut>진행 중(대조 제외)</td>'
                elif r:
                    cells += f'<td>{r["later"]:.2f}</td><td>{r["delta"]:+.2f}</td>'
                elif c["start"] in s["pending"]:
                    cells += '<td colspan=2 class=mut>미도래</td>'
                elif c["start"] in s["missing"]:
                    cells += '<td colspan=2 class=mut>관측 없음</td>'
                elif c["start"] < t["first"]:
                    cells += '<td colspan=2 class=mut>시계열 이전</td>'
                else:
                    cells += '<td colspan=2 class=mut>시작 관측 없음</td>'   # 시계열 안 공백(S6 Codex F5)
            b = t["stats"][hs[0]]["bases"].get(c["start"])
            trs.append(f'<tr><td>{c["start"]}</td><td>{f"{b:.2f}" if b is not None else "—"}</td>{cells}</tr>')
        table = f'<div class=tblwrap><table><tr><th>사이클 시작</th><th>시작 월 값</th>{head}</tr>{"".join(trs)}</table></div>'
    else:
        table = '<p class=mut>부호 일치인 기간이 없어 사이클별 반응 표는 싣지 않는다(브리핑 §4 규칙 — 표 대신 판정 한 줄).</p>'
    return (f'<h3>{t["label"]} <span class=mut>({t["unit"]} · 관측 {t["first"]} ~ {t["last"]} · '
            f'<a href="{html.escape(t["url"])}" {_ga(t["code"])}>{t["source"]}</a>)</span></h3><ul>{summ}</ul>{table}')


def render_cycles_page(report: dict | None, today: str) -> str:
    """cycles.html — 인상사이클 표 · 사이클 시작 후 지표 변화(조건부통계) · 자료 없음 목록 · 방법론. report 없으면 '미수집' 페이지."""
    from blog import build_explorer as be
    from blog.build_site import BASE_URL, ga4_snippet
    from blog.daily_digest import _SITE_CSS
    if report is None:
        body = (f'<p class=meta>{today} 기준 — 기준금리 변경 시계열 <b>미수집</b>(거시 스냅샷 없음 또는 수집 실패). '
                f'표를 만들지 않는다(지어내지 않음).</p>')
        cyc_html = stat_html = ""
    else:
        ongoing = report["cycles"] and report["cycles"][-1]["ongoing"]
        body = (f'<p class=meta>수집 기준일 {report["asof"]} · 기준금리 변경 {report["bok_first"]} ~ {report["bok_last"]} · '
                f'완료 인상사이클 {report["n_completed"]}회{" · 진행 중 1회" if ongoing else ""} · 관측 사실만(자체 해석 없음) · '
                f'<a href="macro.html" {_ga("macro")}>지표 카드 →</a> · <a href="calc.html" {_ga("calc")}>전달 계산기 →</a></p>')
        rows = "".join(f'<tr><td>{c["start"]}</td><td>{c["from_pct"]:.2f}% → {c["first_pct"]:.2f}%</td><td>{c["end"]}</td>'
                       f'<td>{c["peak_pct"]:.2f}%</td><td>{c["n_hikes"]}회</td><td>+{c["total_bp"]}bp</td><td>{c["months"]}개월</td>'
                       f'<td>{"진행 중" if c["ongoing"] else "완료"}</td></tr>' for c in report["cycles"])
        cyc_html = ('<h2>인상사이클 <span class=mut>(기준금리 변경 시계열에서 기계적으로 자른 구간)</span></h2>'
                    '<div class=tblwrap><table><tr><th>시작(첫 인상)</th><th>직전 → 첫 인상</th><th>마지막 인상</th><th>정점</th>'
                    f'<th>인상 횟수</th><th>누적</th><th>기간</th><th>상태</th></tr>{rows}</table></div>')
        miss = "".join(f'<li>{a} — {b}</li>' for a, b in report["missing"])
        stat_html = ('<h2>사이클 시작 후 지표 변화 <span class=mut>(조건부통계 — 과거 n회 중 m회)</span></h2>'
                     + "".join(_target_html(t, report["cycles"]) for t in report["targets"])
                     + f'<h3>자료 없음 <span class=mut>(지어내지 않음)</span></h3><ul>{miss}</ul>')
    page = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>과거 인상사이클 — 서울 부동산 데이터 스냅샷</title>
<meta name=description content="한국은행 기준금리 변경 시계열에서 자른 과거 인상사이클(시작·정점·횟수·누적)과 사이클 시작 후 6·12개월 지표 변화의 과거 n회 중 m회 집계. 자체 해석 없음.">
<style>{_SITE_CSS}</style>
{ga4_snippet()}
</head><body>
<div class=wrap>
<nav class=top><a href="index.html">구 허브</a><a href="explorer.html">탐색기</a><a href="macro.html">거시 지표</a><a href="methodology.html">방법론</a></nav>
<div class=crumb><a href="index.html">서울</a> › <a href="macro.html">거시 지표</a> › 과거 인상사이클</div>
<h1>과거 인상사이클</h1>
{body}
{cyc_html}
{stat_html}
<div class=foot>
인상사이클 = 기준금리 변경 시계열에서 인하(또는 시계열 시작) 뒤 첫 인상부터 다음 인하 전 마지막 인상까지, 기계적으로 자른 구간(국면 라벨 아님).
시작 월 값 = 사이클 시작일 이전 가장 가까운 월 관측(45일 안), +h개월 값 = 시작일의 h개월 뒤 같은 규칙(목표일이 수집 기준일 뒤면 '미도래', 지났는데 관측이 없으면 '관측 없음'). 대조 표본은 완료 사이클만 — 진행 중 사이클은 표에 '진행 중(대조 제외)'. 판정 = 완료 사이클 {MIN_CYCLES}회 이상이고 Δ 부호 전부 같을 때만 '부호 일치' 와 횟수·중위, 그 외엔 '일관된 패턴 없음' 한 줄(사이클별 반응 표 없음).
과거 사이클 수가 적고 사이클마다 물가·환율·규제 조건이 달라 이 표는 다음 사이클에 옮겨 적는 통계가 아니다(표본 밖 한계). 매 빌드 다시 만들며, 값은 기준금리 변경·대상 지표 시계열 갱신(관측 추가·정정)이 있을 때 바뀐다. 매년 1월 월간결산이 '연간 사이클 리포트' 절로 이 표를 정리해 링크한다.<br>
{LAG_ONE_LINER}<br>
{be.DISCLAIMER} {be._takedown()}<br>
<a href="macro.html">거시 지표</a> · <a href="calc.html">전달 계산기</a> · <a href="methodology.html">방법론 전문</a> · <a href="{BASE_URL}/cycles.html">{BASE_URL}/cycles.html</a>
</div>
</div>
</body></html>"""
    assert_lead_wording_ok(_plain(page), "cycles:page")
    return page


def build_macro_regime_section(snapshot: dict | None, today: str) -> dict | None:
    """월간결산 '거시 맥락' 절 자료 — 사실만: 기준금리와 진행 중 인상사이클(시작·횟수·누적), 관측 줄(SECTION_CODES 카드),
    과거 사이클 조건부통계 줄, 전달시차 줄. 국면 라벨 없음. 스냅샷 없거나 MAX_SNAPSHOT_AGE_DAYS 초과면 None(절 생략)."""
    if not snapshot or not snapshot.get("asof"):
        return None
    t = date.fromisoformat(today)
    if (t - date.fromisoformat(snapshot["asof"])).days > MAX_SNAPSHOT_AGE_DAYS:
        return None
    rep = build_cycle_report(snapshot, today)
    cards = {c.code: c for c in build_indicator_cards(snapshot, t)}
    lines: list[str] = []
    if rep:
        cyc = rep["cycles"][-1] if rep["cycles"] else None
        head = f'기준금리 {rep["bok_value"]:.2f}%({rep["bok_last"]} 변경)'
        if cyc and cyc["ongoing"]:
            lines.append(f'{head} — 진행 중인 인상사이클: 시작 {cyc["start"]}({cyc["from_pct"]:.2f}%→{cyc["first_pct"]:.2f}%), '
                         f'인상 {cyc["n_hikes"]}회·누적 +{cyc["total_bp"]}bp')
        else:
            lines.append(f'{head} — 진행 중인 인상사이클 없음'
                         + (f'(마지막 사이클 {cyc["start"]}~{cyc["end"]}, 인상 {cyc["n_hikes"]}회·누적 +{cyc["total_bp"]}bp)' if cyc else ""))
    for code in SECTION_CODES:
        c = cards.get(code)
        if c:
            lines.append(f'{c.label} {c.value_txt}({c.date}) · 직전 대비 {c.delta_prev_txt} · 1년 전 대비 {c.delta_12m_txt}'
                         + (f' · {c.streak_txt}' if c.streak_txt else "") + (f' · 다음 발표 {c.next_release_txt}' if c.next_release_txt else ""))
    stat_lines: list[str] = []
    if rep:
        for tg in rep["targets"]:
            if tg.get("missing"):
                continue
            for h, s in tg["stats"].items():
                stat_lines.append(f'과거 인상사이클 시작 {h}개월 후 {tg["label"]}: {stat_txt(s, _delta_unit(tg["unit"]))}')
    if not lines:
        return None
    for x in lines + stat_lines:
        assert_lead_wording_ok(x, "macro_regime_section")
    return {"asof": snapshot["asof"], "lines": lines, "stat_lines": stat_lines,
            "n_cycles": rep["n_completed"] if rep else 0, "lag_line": LAG_ONE_LINER}
