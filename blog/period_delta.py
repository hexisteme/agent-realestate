"""주간결산(WeeklyDelta)·월간결산(MonthlyDelta) — 일요일 / 월 마지막 일요일에 발행되는 기간 변화 포스트(2026-09-07).

배경: 일간 "오늘의 변화"(daily_digest)는 그날 스냅샷의 목록(52주 상단/하단·전세가율·회전율)이지 전일 대비 diff 가
아니고, 구별 주간 리포트(월요일, render_gu_weekly_post)도 구허브 스냅샷의 재게시다. 여기서 처음으로 두 스냅샷
(dataset.json 보관본, blog/snapshots.py)을 **같은 단지끼리 짝지어**(구·표시명·전용) 변화량을 낸다:
  · 가격대(PriceBand, build_explorer.PRICE_SEGMENTS) 4밴드별 단지 수·12개월 중위의 중위·단지별 Δ중위(중위·IQR·n)·밴드 이동(유입/유출)
  · 밴드 이동 단지 명단(공공 실거래 실명 — 사실만)
  · 구별 25행: 구 중위 기준→이번(Δ%)·단지별 Δ중위(n)·52주 상단/하단 근접 수 기준→이번
  · 신고 델타(diff_filings): MOLIT 원본 아카이브(snapshots/molit)가 base 날짜에 있을 때만 — 없으면 절 생략(지어내지 않음)
  · 월간 = 같은 표 + 그 달 주차별(일요일 스냅샷) 행 + 이번 주 절
규칙(A모델): 결정론·템플릿·LLM 없음. 평가어·서수·원인·전망 금지(wording_guard), 수치엔 n 병기, 짝 n<MIN_PAIRS 셀은 "—".
base 의 밴드는 항상 12개월 중위(molit_recent_eok)에서 **재계산** — 09-06 이전 스냅샷의 price_segment 는 옛 6/10/15 라벨이다.
발행 경로: run_daily(write_posts 뒤) → posts/{today}-주간결산.html(+claims.jsonl) + tistory/{today}-periodic-tistory-draft.html →
build_site 의 posts/* glob 으로 sitemap-posts·feed·archive 자동, cron_daily.sh 가 일요일 2편째로 티스토리 발행(kind=periodic).
"""
from __future__ import annotations
import json
import os
import statistics as st
from collections import Counter, defaultdict
from datetime import date, timedelta
from urllib.parse import quote

import blog.build_explorer as be
from blog.build_site import BASE_URL, ga4_snippet
from blog.daily_digest import _SITE_CSS
from blog.snapshots import (is_last_sunday_of_month, list_snapshot_dates, load_molit_archive_on, load_snapshot_on,
                            snapshot_date_of, snapshot_path_on)
from blog.tistory_draft import _MUT, _TBL, _TD, _TH, TISTORY_TAGS
from blog.wording_guard import assert_wording_ok

BANDS = [label for _, label in be.PRICE_SEGMENTS]
POST_LABEL = {"weekly": "주간결산", "monthly": "월간결산"}
MIN_PAIRS = 5                 # 셀 표본 하한 — 짝지은 단지 수가 이보다 적으면 "—"
HI_POS, LO_POS = 99, 6        # daily_digest 와 동일 임계(52주 위치 %)
MIN_MONTHLY_SPAN_DAYS = 21    # 전월 마지막 일요일 스냅샷이 없을 때 대체 base 의 최소 기간
TISTORY_BUDGET = 30000
# 티스토리 본문 축약 단계 — 예산(TISTORY_BUDGET) 안에 들 때까지 0→4 순서로 낮춘다(사이트 본문은 항상 0단계 전체;
# 티스토리 본문 끝의 "사이트 원문" 링크가 잘린 표를 받는다). 실측(2026-09-07 프리뷰 927단지): 월간 0단계 30,566B.
_TRIM_LEVELS = [
    dict(pos_cols=True,  week_table=True,  mig=40, highs=15, week_bands=True,  gu_full=True),
    dict(pos_cols=False, week_table=False, mig=40, highs=15, week_bands=True,  gu_full=True),
    dict(pos_cols=False, week_table=False, mig=20, highs=8,  week_bands=True,  gu_full=True),
    dict(pos_cols=False, week_table=False, mig=10, highs=0,  week_bands=False, gu_full=True),
    dict(pos_cols=False, week_table=False, mig=5,  highs=0,  week_bands=False, gu_full=False),
]
MACRO_SECTION_TISTORY = False   # 월간 '거시 맥락' 절(S5, L3) — 첫 회차 사람 검토(계획 게이트 ④) 통과 후 True. 그 전엔 site 원문에만
MAX_MIGRATION_ROWS = _TRIM_LEVELS[0]["mig"]
MAX_RECORD_HIGH_ROWS = _TRIM_LEVELS[0]["highs"]


# ── 달력 ─────────────────────────────────────────────────────────────────

def is_last_sunday(today: str) -> bool:
    return is_last_sunday_of_month(date.fromisoformat(today))


def period_kind(today: str) -> str | None:
    """오늘 쓸 결산: 'monthly'(월 마지막 일요일) · 'weekly'(그 외 일요일) · None.
    env RE_PERIODIC_POSTS: weekly|monthly = 요일 무관 강제, 0 = 억제, 그 외/미설정 = 달력 판정."""
    ov = os.environ.get("RE_PERIODIC_POSTS")
    if ov in ("weekly", "monthly"):
        return ov
    if ov == "0":
        return None
    d = date.fromisoformat(today)
    if d.weekday() != 6:
        return None
    return "monthly" if is_last_sunday_of_month(d) else "weekly"


def previous_month_last_sunday(today: str) -> date:
    d = date.fromisoformat(today)
    last_prev = d.replace(day=1) - timedelta(days=1)
    return last_prev - timedelta(days=(last_prev.weekday() + 1) % 7)


def resolve_base(kind: str, today: str, dir: str) -> tuple[date, str] | None:
    """(base 날짜, 스냅샷 경로). weekly = 7일 전(±1). monthly = 전월 마지막 일요일(±1) → 없으면
    ≥MIN_MONTHLY_SPAN_DAYS 전 가장 오래된 일요일 스냅샷(기간은 본문에 명기) → 그것도 없으면 None(발행 skip)."""
    d = date.fromisoformat(today)
    if kind == "weekly":
        p = snapshot_path_on(d - timedelta(days=7), 1, dir)
        return (snapshot_date_of(p), p) if p else None
    p = snapshot_path_on(previous_month_last_sunday(today), 1, dir)
    if p:
        return (snapshot_date_of(p), p)
    cands = [x for x in list_snapshot_dates(dir) if x.weekday() == 6 and (d - x).days >= MIN_MONTHLY_SPAN_DAYS]
    if not cands:
        return None
    p = snapshot_path_on(cands[0], 0, dir)
    return (cands[0], p) if p else None


# ── 짝짓기·집계 ─────────────────────────────────────────────────────────────

def _key(r: dict) -> tuple:
    return (r.get("gu"), r.get("name"), r.get("area_m2"))


def _band(r: dict) -> str | None:
    return be.price_segment(r.get("molit_recent_eok"))


def _pct(cur: float | None, base: float | None) -> float | None:
    if not cur or not base:
        return None
    return round((cur - base) / base * 100, 1)


def _median_iqr(vals: list[float]) -> tuple[float | None, float | None]:
    if len(vals) < MIN_PAIRS:
        return None, None
    q1, _, q3 = st.quantiles(sorted(vals), n=4, method="inclusive")
    return round(st.median(vals), 1), round(q3 - q1, 1)


def pair_complexes(cur_ds: dict, base_ds: dict) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """(짝 [(cur,base)], 이번에만 있는 단지, 기준에만 있던 단지) — 키 = (구, 표시명, 전용)."""
    base = {_key(r): r for r in base_ds.get("complexes", [])}
    pairs, added = [], []
    for r in cur_ds["complexes"]:
        b = base.get(_key(r))
        if b is None:
            added.append(r)
        else:
            pairs.append((r, b))
    cur_keys = {_key(r) for r in cur_ds["complexes"]}
    removed = [b for k, b in base.items() if k not in cur_keys]
    return pairs, added, removed


def compute_band_deltas(cur_ds: dict, base_ds: dict) -> list[dict]:
    pairs, _, _ = pair_complexes(cur_ds, base_ds)
    rows = []
    for band in BANDS:
        cur_rows = [r for r in cur_ds["complexes"] if _band(r) == band]
        base_rows = [r for r in base_ds["complexes"] if _band(r) == band]
        deltas = [v for v in (_pct(c.get("molit_recent_eok"), b.get("molit_recent_eok"))
                              for c, b in pairs if _band(c) == band) if v is not None]
        med, iqr = _median_iqr(deltas)
        rows.append({"band": band, "n_cur": len(cur_rows), "n_base": len(base_rows),
                     "median_cur": be.compute_gu_median(cur_rows), "median_base": be.compute_gu_median(base_rows),
                     "delta_median_pct": med, "delta_iqr_pct": iqr, "n_pairs": len(deltas),
                     "moved_in": sum(1 for c, b in pairs if _band(c) == band and _band(b) not in (None, band)),
                     "moved_out": sum(1 for c, b in pairs if _band(b) == band and _band(c) not in (None, band))})
    return rows


def compute_band_migrations(cur_ds: dict, base_ds: dict) -> list[dict]:
    pairs, _, _ = pair_complexes(cur_ds, base_ds)
    out = []
    for c, b in pairs:
        fb, tb = _band(b), _band(c)
        if fb and tb and fb != tb:
            out.append({"name": c["name"], "gu": c["gu"], "area_m2": c.get("area_m2"), "from": fb, "to": tb,
                        "base_eok": b["molit_recent_eok"], "cur_eok": c["molit_recent_eok"], "n": c.get("molit_n"),
                        "direction": "up" if BANDS.index(tb) > BANDS.index(fb) else "down"})
    return sorted(out, key=lambda x: (x["gu"], x["name"]))


def _count_pos(rows: list[dict], hi: bool = True) -> int:
    return sum(1 for r in rows if be.passes_rank_gate(r) and r.get("molit_pos_52w") is not None
               and ((r["molit_pos_52w"] >= HI_POS) if hi else (r["molit_pos_52w"] <= LO_POS)))


def compute_gu_deltas(cur_ds: dict, base_ds: dict) -> list[dict]:
    by_c, by_b = defaultdict(list), defaultdict(list)
    for r in cur_ds["complexes"]:
        by_c[r["gu"]].append(r)
    for r in base_ds["complexes"]:
        by_b[r["gu"]].append(r)
    dl = defaultdict(list)
    for c, b in pair_complexes(cur_ds, base_ds)[0]:
        v = _pct(c.get("molit_recent_eok"), b.get("molit_recent_eok"))
        if v is not None:
            dl[c["gu"]].append(v)
    rows = []
    for gu in sorted(set(by_c) | set(by_b)):
        mc, mb = be.compute_gu_median(by_c[gu]), be.compute_gu_median(by_b[gu])
        med, _ = _median_iqr(dl[gu])
        rows.append({"gu": gu, "n_cur": len(by_c[gu]), "n_base": len(by_b[gu]), "median_cur": mc, "median_base": mb,
                     "gu_median_delta_pct": _pct(mc, mb), "delta_median_pct": med, "n_pairs": len(dl[gu]),
                     "hi_cur": _count_pos(by_c[gu]), "hi_base": _count_pos(by_b[gu]),
                     "lo_cur": _count_pos(by_c[gu], hi=False), "lo_base": _count_pos(by_b[gu], hi=False)})
    return rows


# ── 신고 델타(FilingDelta 최소형) ─────────────────────────────────────────────

def _filing_key(rec: dict) -> tuple:
    return (rec.get("apt"), round(float(rec.get("area") or 0), 2), int(rec.get("price") or 0), rec.get("ym"))


def diff_filings(prev: dict, cur: dict) -> dict:
    """MOLIT 원본 두 시점의 multiset 차. cur 에만 있는 레코드 = 그 사이 새로 확인된 신고(체결일이 아니라 API 반영
    시점), prev 에만 있던 레코드 = 사라진 건(해제·정정). 레코드 신원 = (apt, area, price, ym) — 현재 수집기가
    거래일·층을 버려 동일 튜플의 중복은 개수 차로 처리한다(P1 FilingDelta 가 필드 보존 뒤 정밀화).
    record_highs = 같은 단지·같은 전용(반올림)의 prev 12개월 표본(≥3건) 최고가를 넘는 신규 레코드."""
    lawd_gu = {v: k for k, v in be.GU_LAWD.items()}
    new, highs, gone = [], [], 0
    for lawd, recs in cur.items():
        if lawd == "_done" or not isinstance(recs, list):
            continue
        prev_recs = [r for r in (prev.get(lawd) or []) if isinstance(r, dict)]
        pc, cc = Counter(_filing_key(r) for r in prev_recs), Counter(_filing_key(r) for r in recs)
        prev_by_apt: dict[tuple, list[int]] = defaultdict(list)
        for r in prev_recs:
            prev_by_apt[(r.get("apt"), round(float(r.get("area") or 0)))].append(int(r.get("price") or 0))
        gu = lawd_gu.get(lawd, lawd)
        for (apt, area, price, ym), n in (cc - pc).items():
            new.extend([{"gu": gu, "lawd": lawd, "apt": apt, "area": area, "price": price, "ym": ym}] * n)
            hist = prev_by_apt.get((apt, round(area)), [])
            if len(hist) >= 3 and price > max(hist):
                highs.append({"gu": gu, "apt": apt, "area": area, "price": price, "prev_max": max(hist), "n_prev": len(hist)})
        gone += sum((pc - cc).values())
    return {"new": new, "n_new": len(new), "n_gone": gone,
            "record_highs": sorted(highs, key=lambda x: (x["gu"], x["apt"]))}


def summarize_filings(f: dict) -> dict:
    by_band: dict[str, list[float]] = defaultdict(list)
    by_gu: Counter = Counter()
    for r in f["new"]:
        by_band[be.price_segment(r["price"] / 1e8)].append(r["price"] / 1e8)
        by_gu[r["gu"]] += 1
    return {"n_new": f["n_new"], "n_gone": f["n_gone"], "by_gu": dict(by_gu),
            "bands": [{"band": b, "n": len(by_band[b]),
                       "median_eok": round(st.median(by_band[b]), 2) if by_band[b] else None} for b in BANDS],
            "record_highs": f.get("record_highs", [])}


# ── 조립 ─────────────────────────────────────────────────────────────────

def weekly_rows_between(base_date: date, today: str, dir: str) -> list[dict]:
    """월간용 주차별 행 — base 이후 오늘까지의 일요일 스냅샷마다 단지 수·서울 중위·직전 주 대비 Δ·밴드별 단지 수."""
    d = date.fromisoformat(today)
    rows, prev_med = [], None
    prev = load_snapshot_on(base_date, 0, dir)
    if prev:
        prev_med = be.compute_gu_median(prev["complexes"])
    for x in [x for x in list_snapshot_dates(dir) if x.weekday() == 6 and base_date < x <= d]:
        ds = load_snapshot_on(x, 0, dir)
        if not ds:
            continue
        med = be.compute_gu_median(ds["complexes"])
        rows.append({"date": x.isoformat(), "n": len(ds["complexes"]), "seoul_median": med,
                     "delta_pct": _pct(med, prev_med),
                     "bands": {b: sum(1 for r in ds["complexes"] if _band(r) == b) for b in BANDS}})
        prev_med = med
    return rows


def _group_median(rows: list[dict], field: str, key: str, min_n: int = 5, nd: int = 2) -> list[dict]:
    from statistics import median
    g: dict = {}
    for r in rows:
        g.setdefault(r.get(key) or "—", []).append(r[field])
    return [{"key": k, "n": len(v), "median": round(median(v), nd) if len(v) >= min_n else None} for k, v in g.items()]


def summarize_fact_ratio(rows: list[dict], field: str, nd: int = 2, gu_min_n: int = 5) -> dict:
    """월간결산 사실 비율 요약(공시가 배율·평형 격차 공용) — 전체 n·중위, 가격대별(BANDS 순), 구별(가나다 순, n<gu_min_n 은 중위 —).
    공시가 배율은 브리핑 §2.1 커버리지 게이트대로 구별 n≥10(S4 Codex)."""
    from statistics import median
    vals = [r for r in rows if r.get(field) is not None]
    bands = {b["key"]: b for b in _group_median(vals, field, "price_segment", nd=nd)}
    return {"n": len(vals), "n_total": len(rows),
            "median": round(median([r[field] for r in vals]), nd) if len(vals) >= 5 else None,
            "bands": [bands.get(b, {"key": b, "n": 0, "median": None}) for b in BANDS],
            "gus": sorted(_group_median(vals, field, "gu", min_n=gu_min_n, nd=nd), key=lambda x: x["key"])}


def build_period_delta(cur_ds: dict, base_ds: dict, kind: str, today: str, base_date: date,
                       filings: dict | None = None, weekly: dict | None = None,
                       weekly_rows: list[dict] | None = None, macro_section: dict | None = None) -> dict:
    pairs, added, removed = pair_complexes(cur_ds, base_ds)
    return {"kind": kind, "today": today, "asof": cur_ds.get("data_asof", today), "base_date": base_date.isoformat(),
            "base_asof": base_ds.get("data_asof"), "n_cur": len(cur_ds["complexes"]), "n_base": len(base_ds["complexes"]),
            "n_pairs": len(pairs),
            "added": sorted(f'{r["name"]}({r["gu"]})' for r in added),
            "removed": sorted(f'{r["name"]}({r["gu"]})' for r in removed),
            "seoul_median_cur": be.compute_gu_median(cur_ds["complexes"]),
            "seoul_median_base": be.compute_gu_median(base_ds["complexes"]),
            "bands": compute_band_deltas(cur_ds, base_ds), "migrations": compute_band_migrations(cur_ds, base_ds),
            "gus": compute_gu_deltas(cur_ds, base_ds),
            "filings": summarize_filings(filings) if filings else None,
            "weekly": weekly, "weekly_rows": weekly_rows,
            # 월간만: 공시가 배율·평형 격차(S4, 사실 비율 분포 — 원인·판단 없음)
            "gongsi_multiple": summarize_fact_ratio(cur_ds["complexes"], "gongsi_multiple", gu_min_n=10) if kind == "monthly" else None,
            "area_spread": summarize_fact_ratio(cur_ds["complexes"], "area_spread_59_84_pct", nd=1) if kind == "monthly" else None,
            # 월간만: 거시 맥락(S5 L3 — macro_cycles.build_macro_regime_section 결과, 사실 줄·조건부통계 줄·시차 줄)
            "macro_context": macro_section if kind == "monthly" else None}


# ── 렌더 ─────────────────────────────────────────────────────────────────

def _eok(v: float | None) -> str:
    return f"{v:g}억" if v is not None else "—"


def _pct_txt(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def _arrow(a, b) -> str:
    return f"{_eok(a)} → {_eok(b)}"


def _tbl(headers: list[str], rows: list[list[str]], inline: bool) -> str:
    if inline:
        head = "".join(f'<td style="{_TH}"><b>{h}</b></td>' for h in headers)
        body = "".join("<tr>" + "".join(f'<td style="{_TD}">{c}</td>' for c in r) + "</tr>" for r in rows)
        return f'<table style="{_TBL}"><tr>{head}</tr>{body}</table>'
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<div class=tblwrap><table><tr>{head}</tr>{body}</table></div>"


def _h(text: str, inline: bool) -> str:
    return f"<p><b>{text}</b></p>" if inline else f"<h2>{text}</h2>"


def _p(text: str, inline: bool, mut: bool = False) -> str:
    if inline:
        return f'<p style="{_MUT}">{text}</p>' if mut else f"<p>{text}</p>"
    return f"<p class=mut>{text}</p>" if mut else f"<p>{text}</p>"


def _gu_link(gu: str, inline: bool) -> str:
    href = f"{BASE_URL}/gu/{quote(gu)}.html" if inline else f"../gu/{quote(gu)}.html"
    return f'<a href="{href}">{gu}</a>'


def _band_table(bands: list[dict], inline: bool) -> str:
    rows = [[b["band"], f'{b["n_base"]} → {b["n_cur"]}', _arrow(b["median_base"], b["median_cur"]),
             (f'{_pct_txt(b["delta_median_pct"])} (IQR {b["delta_iqr_pct"]:g}%p, n{b["n_pairs"]})'
              if b["delta_median_pct"] is not None else f'— (n{b["n_pairs"]})'),
             f'유입 {b["moved_in"]} · 유출 {b["moved_out"]}'] for b in bands]
    return _tbl(["가격대", "단지 수(기준→이번)", "중위(억, 기준→이번)", "단지별 Δ중위", "밴드 이동"], rows, inline)


def _sections(d: dict, inline: bool, level: int = 0) -> str:
    opt = _TRIM_LEVELS[level]
    label = POST_LABEL[d["kind"]]
    span = (date.fromisoformat(d["today"]) - date.fromisoformat(d["base_date"])).days
    parts = [_p(f'기준 스냅샷 {d["base_date"]}(실거래 기준일 {d["base_asof"] or "—"}) → 이번 {d["today"]}(기준일 {d["asof"]}), {span}일. '
                f'발행 {d["n_base"]} → {d["n_cur"]}단지, 같은 단지로 짝지은 {d["n_pairs"]}단지 기준. 국토부 실거래 12개월 동일평형 중위의 변화(사실), '
                f'자체 점수·순위 없음.', inline)]
    if d["added"] or d["removed"]:
        add_txt = (", ".join(d["added"][:10]) + (f' 외 {len(d["added"]) - 10}' if len(d["added"]) > 10 else "")) if d["added"] else "없음"
        rem_txt = (", ".join(d["removed"][:10]) + (f' 외 {len(d["removed"]) - 10}' if len(d["removed"]) > 10 else "")) if d["removed"] else "없음"
        parts.append(_p(f'이번에 새로 포함 {len(d["added"])}: {add_txt} · 기준에만 있던 단지 {len(d["removed"])}: {rem_txt}', inline, mut=True))
    parts.append(_h("서울 전체 · 가격대별", inline))
    parts.append(_p(f'서울 중위(게이트 통과 단지 중위의 중위) {_arrow(d["seoul_median_base"], d["seoul_median_cur"])} '
                    f'({_pct_txt(_pct(d["seoul_median_cur"], d["seoul_median_base"]))})', inline))
    parts.append(_band_table(d["bands"], inline))
    mig, cap = d["migrations"], opt["mig"]
    parts.append(_h(f"가격대 이동 단지 {len(mig)}곳", inline))
    if mig:
        rows = [[f'{m["name"]}({_gu_link(m["gu"], inline)}) {m["area_m2"]:g}㎡' if m["area_m2"] else f'{m["name"]}({_gu_link(m["gu"], inline)})',
                 f'{m["from"]} → {m["to"]}', _arrow(m["base_eok"], m["cur_eok"]), f'{_pct_txt(_pct(m["cur_eok"], m["base_eok"]))} n{m["n"]}']
                for m in mig[:cap]]
        parts.append(_tbl(["단지(구) 전용", "가격대", "중위(억, 기준→이번)", "Δ n"], rows, inline))
        if len(mig) > cap:
            parts.append(_p(f'명단은 구·단지명 순 {cap}곳까지 표시(전체 {len(mig)}곳).', inline, mut=True))
    else:
        parts.append(_p("가격대가 바뀐 단지 없음", inline, mut=True))
    parts.append(_h("구별 (25개 구)", inline))
    fil = d["filings"]
    if opt["gu_full"]:
        heads = (["구", "단지 수", "구 중위(억, 기준→이번)", "Δ", "단지별 Δ중위"]
                 + (["52주 상단(기준→이번)", "52주 하단(기준→이번)"] if opt["pos_cols"] else []))
    else:
        heads = ["구", "구 중위(억, 기준→이번)", "Δ"]
    heads += ["신고"] if fil else []
    rows = []
    for g in d["gus"]:
        if opt["gu_full"]:
            row = [_gu_link(g["gu"], inline), f'{g["n_base"]} → {g["n_cur"]}', _arrow(g["median_base"], g["median_cur"]),
                   _pct_txt(g["gu_median_delta_pct"]),
                   f'{_pct_txt(g["delta_median_pct"])} n{g["n_pairs"]}' if g["delta_median_pct"] is not None else f'— n{g["n_pairs"]}']
            if opt["pos_cols"]:
                row += [f'{g["hi_base"]} → {g["hi_cur"]}', f'{g["lo_base"]} → {g["lo_cur"]}']
        else:
            row = [_gu_link(g["gu"], inline), _arrow(g["median_base"], g["median_cur"]), _pct_txt(g["gu_median_delta_pct"])]
        if fil:
            row.append(f'{fil["by_gu"].get(g["gu"], 0)}건')
        rows.append(row)
    parts.append(_tbl(heads, rows, inline))
    parts.append(_h("신고 델타", inline))
    if fil:
        parts.append(_p(f'기준 아카이브 대비 새로 확인된 신고 {fil["n_new"]}건 · 사라진 건(해제·정정) {fil["n_gone"]}건. '
                        f'신고 시점 기준이며 체결일이 아님(신고 지연 최대 30일).', inline))
        parts.append(_tbl(["가격대(신고가)", "건수", "중위(억)"],
                          [[b["band"], str(b["n"]), _eok(b["median_eok"])] for b in fil["bands"]], inline))
        highs, hcap = fil["record_highs"], opt["highs"]
        if highs:
            note = f'(구·단지명 순 {hcap}건 표시)' if 0 < hcap < len(highs) else ('(명단은 사이트 원문)' if hcap == 0 else '')
            parts.append(_p(f'같은 단지·전용의 기준 12개월 표본 최고가를 넘긴 신고 {len(highs)}건{note}', inline))
            if hcap:
                parts.append(_tbl(["단지(구) 전용", "신고가(억)", "기준 최고(억) n"],
                                  [[f'{h["apt"]}({_gu_link(h["gu"], inline)}) {h["area"]:g}㎡', _eok(round(h["price"] / 1e8, 2)),
                                    f'{_eok(round(h["prev_max"] / 1e8, 2))} n{h["n_prev"]}'] for h in highs[:hcap]], inline))
    else:
        parts.append(_p("기준 날짜의 실거래 원본 아카이브가 없어 이번 회차엔 신고 델타를 내지 않음(아카이브는 매일 보관, 다음 회차부터).", inline, mut=True))
    if d["kind"] == "monthly":
        parts.append(_h("주차별", inline))
        if d["weekly_rows"]:
            bcols = BANDS if opt["week_bands"] else []
            parts.append(_tbl(["일요일", "단지 수", "서울 중위(억)", "직전 주 대비"] + bcols,
                              [[w["date"], str(w["n"]), _eok(w["seoul_median"]), _pct_txt(w["delta_pct"])]
                               + [str(w["bands"][b]) for b in bcols] for w in d["weekly_rows"]], inline))
        else:
            parts.append(_p("이 달의 일요일 스냅샷이 없어 주차별 표 생략.", inline, mut=True))
        if d["weekly"]:
            w = d["weekly"]
            parts.append(_h(f'이번 주({w["base_date"]} → {w["today"]})', inline))
            if opt["week_table"]:
                parts.append(_band_table(w["bands"], inline))
            else:
                parts.append(_p(" · ".join(f'{b["band"]} {b["n_base"]}→{b["n_cur"]}단지 {_pct_txt(b["delta_median_pct"])}(n{b["n_pairs"]})'
                                           for b in w["bands"]), inline))
            parts.append(_p(f'가격대 이동 {len(w["migrations"])}곳 · 짝 {w["n_pairs"]}단지.', inline, mut=True))
        gm, sp = d.get("gongsi_multiple"), d.get("area_spread")
        _mult = lambda v: f"×{v:.2f}" if v is not None else "—"                     # noqa: E731
        if gm and gm["n"]:
            parts.append(_h("공시가 배율", inline))
            parts.append(_p(f'국토부 실거래 12개월 동일평형 중위 ÷ 같은 평형 공시가격. 공시가를 찾은 면적과 발행 면적이 일치하는 {gm["n"]}/{gm["n_total"]}단지, '
                            f'전체 중위 {_mult(gm["median"])}. 보유세 산정 기준 대비 시세 배율의 사실이며 가치 판단이 아님. 구별 표는 n≥10 인 구만 중위를 적음.', inline))
            parts.append(_tbl(["가격대", "단지 수", "배율 중위"], [[b["key"], str(b["n"]), _mult(b["median"])] for b in gm["bands"]], inline))
            if opt["gu_full"]:
                parts.append(_tbl(["구", "단지 수", "배율 중위"], [[_gu_link(g["key"], inline), str(g["n"]), _mult(g["median"])] for g in gm["gus"]], inline))
        if sp and sp["n"]:
            parts.append(_h("평형 격차 59↔84", inline))
            parts.append(_p(f'같은 단지 59㎡대와 84㎡대(각 ±3.5㎡, 양쪽 n≥5)의 ㎡당 실거래 중위 비(59÷84−1). 계산 가능 {sp["n"]}/{sp["n_total"]}단지, '
                            f'전체 중위 {_pct_txt(sp["median"])}. 격차의 사실만 적고 원인은 적지 않음.', inline))
            parts.append(_tbl(["가격대", "단지 수", "격차 중위"], [[b["key"], str(b["n"]), _pct_txt(b["median"])] for b in sp["bands"]], inline))
            if opt["gu_full"]:
                parts.append(_tbl(["구", "단지 수", "격차 중위"], [[_gu_link(g["key"], inline), str(g["n"]), _pct_txt(g["median"])] for g in sp["gus"]], inline))
        mc = d.get("macro_context")
        if mc and (not inline or MACRO_SECTION_TISTORY):                        # 게이트 ④ 전엔 site 원문에만
            annual = date.fromisoformat(d["today"]).month == 1                   # 매년 1월 월간결산 = 연간 사이클 리포트(계획 §0)
            parts.append(_h("거시 맥락 · 연간 사이클 리포트" if annual else "거시 맥락", inline))
            parts.append(_p(f'거시 지표 수집 기준일 {mc["asof"]}. 관측 사실과 과거 집계만(국면 라벨·자체 해석 없음).', inline, mut=True))
            for x in mc["lines"] + mc["stat_lines"]:
                parts.append(_p(x, inline))
            cyc_href = f"{BASE_URL}/cycles.html" if inline else "../cycles.html"
            parts.append(_p(f'{mc["lag_line"]} 인상사이클 표·방법론: <a href="{cyc_href}">과거 인상사이클</a>.', inline, mut=True))
    if inline and level > 0:
        parts.append(_p("지면 한도로 일부 표·명단을 줄였다 — 전체 표는 아래 사이트 원문에.", inline, mut=True))
    parts.append(_p(f'방법론: 12개월 동일평형(±3.5㎡) 실거래 중위는 이동 창이라 {label} 변화가 대부분 작다 — 변화의 대부분은 창에 들어오고 '
                    f'나가는 신고다. 단지별 Δ = (이번 중위 − 기준 중위) ÷ 기준 중위, 짝지은 단지 {MIN_PAIRS}곳 미만인 셀은 "—". '
                    f'52주 상단/하단 = 최근 3개월 중위가 12개월 레인지의 {HI_POS}% 이상 / {LO_POS}% 이하(아파트·전용 40㎡ 이상·표본 10건 이상만). '
                    f'가격대 = 12개월 중위 기준 반개구간(10억 미만 / 10~15억 / 15~20억 / 20억 이상). 국토부 RTMS 공공데이터, 민간 시세는 사용·게재하지 않음. '
                    f'{be.DISCLAIMER} {be._takedown()}', inline, mut=True))
    return "".join(parts)


def render_period_post(d: dict) -> dict:
    label = POST_LABEL[d["kind"]]
    title = f'서울 아파트 {label} — {d["today"]} · {d["base_date"]} 대비 · {d["n_cur"]}단지'
    desc = (f'서울 아파트 {d["n_cur"]}단지 국토부 공공 실거래 12개월 중위의 {label} 변화({d["base_date"]}→{d["asof"]}). '
            f'가격대·구별 단지 수와 중위 변화, 가격대 이동 {len(d["migrations"])}곳. 자체 평가·점수·순위 없음, 투자자문 아님.')
    body_site = _sections(d, inline=False, level=0)
    jsonld = {"@context": "https://schema.org", "@type": "Dataset", "name": title, "dateModified": d["today"],
              "datePublished": d["today"], "description": desc, "license": "https://creativecommons.org/licenses/by-nc/4.0/",
              "creator": {"@type": "Organization", "name": "agent_realestate (개인 연구)"}, "isAccessibleForFree": True,
              "keywords": ["부동산", "실거래", "공공데이터", "서울", label]}
    html_out = f"""<!DOCTYPE html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name=description content="{desc}">
<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>
<style>{_SITE_CSS}</style>
{ga4_snippet()}
</head><body>
<div class=wrap>
<nav class=top><a href="../index.html">구 허브</a><a href="../explorer.html">탐색기</a>
<a href="../daily/latest.html">오늘의 변화</a><a href="../methodology.html">방법론</a></nav>
<div class=crumb><a href="../index.html">서울</a> › {label}</div>
<h1>서울 아파트 {label}</h1>
<p class=meta>{d["base_date"]} → {d["today"]} · 발행 {d["n_cur"]}단지 · 짝 {d["n_pairs"]}단지</p>
{body_site}
<div class=foot><a href="../methodology.html">방법론 전문</a> · <a href="../explorer.html">탐색기</a> ·
코드: <a href="https://github.com/hexisteme/agent-realestate">agent-realestate</a></div>
</div>
</body></html>"""
    footer = (f'<p><a href="{BASE_URL}/posts/{quote(d["today"] + "-" + label)}.html">사이트 원문</a> · '
              f'<a href="{BASE_URL}/">전체 탐색기·인덱스</a> · <a href="{BASE_URL}/methodology.html">방법론 전문</a></p>')
    for level in range(len(_TRIM_LEVELS)):
        tistory_html = _sections(d, inline=True, level=level) + footer
        if len(tistory_html.encode("utf-8")) <= TISTORY_BUDGET:
            break
    else:
        raise ValueError(f"[period_delta] 최소 축약(_TRIM_LEVELS 끝)도 {TISTORY_BUDGET}B 초과 — 단계를 추가하세요")
    assert_wording_ok(html_out, f"period_delta:{label}:site")
    assert_wording_ok(tistory_html, f"period_delta:{label}:tistory")
    claims = [{"claim": f"{d['kind']}_band_change", "band": b["band"], "n_base": b["n_base"], "n_cur": b["n_cur"],
               "median_base_eok": b["median_base"], "median_cur_eok": b["median_cur"], "delta_median_pct": b["delta_median_pct"],
               "n_pairs": b["n_pairs"], "grade": "fact", "source": "MOLIT_RTMS_public", "base": d["base_date"], "asof": d["asof"]}
              for b in d["bands"]]
    claims += [{"claim": f"{d['kind']}_gu_change", "gu": g["gu"], "median_base_eok": g["median_base"], "median_cur_eok": g["median_cur"],
                "delta_pct": g["gu_median_delta_pct"], "n_pairs": g["n_pairs"], "grade": "fact", "source": "MOLIT_RTMS_public",
                "base": d["base_date"], "asof": d["asof"]} for g in d["gus"]]
    claims += [{"claim": "price_band_migration", "name": m["name"], "gu": m["gu"], "area_m2": m["area_m2"], "from": m["from"],
                "to": m["to"], "base_eok": m["base_eok"], "cur_eok": m["cur_eok"], "n": m["n"], "grade": "fact",
                "source": "MOLIT_RTMS_public", "base": d["base_date"], "asof": d["asof"]} for m in d["migrations"]]
    for key, name, fld in (("gongsi_multiple", "monthly_gongsi_multiple_band", "median_multiple"), ("area_spread", "monthly_area_spread_band", "median_spread_pct")):
        if d.get(key) and d[key]["n"]:                      # 값 없으면 claim 도 없음(절과 동일)
            claims += [{"claim": name, "band": b["key"], "n": b["n"], fld: b["median"]} for b in d[key]["bands"] if b["median"] is not None]   # 중위 없는 밴드는 claim 없음(S4 Codex)
    if d.get("macro_context"):
        mc = d["macro_context"]
        claims.append({"claim": "monthly_macro_context", "asof": mc["asof"], "n_cycles": mc["n_cycles"], "lines": mc["lines"],
                       "stat_lines": mc["stat_lines"], "grade": "fact", "source": "BOK/FRED/네이버금융(macro 스냅샷)", "site_only": not MACRO_SECTION_TISTORY})
    return {"title": title, "description": desc, "tags": TISTORY_TAGS + f",{label}", "html": html_out,
            "tistory_html": tistory_html, "tistory_level": level, "claims": claims}


def write_period_post(ds: dict, today: str, outdir: str = "report/blog", molit_path: str | None = None,
                      macro_snapshot: dict | None = None) -> dict:
    """run_daily 호출점. 반환: {"kind","post","draft","base_date","n_pairs"} 또는 {"skipped": 사유}.
    티스토리 원고는 kind=periodic 으로 따로 써서(daily 원고·마커와 분리) cron_daily.sh 가 일요일 2편째로 발행한다."""
    kind = period_kind(today)
    if not kind:
        return {"skipped": "no_period_today"}
    snap_dir = f"{outdir}/snapshots"
    base = resolve_base(kind, today, snap_dir)
    if not base:
        return {"skipped": f"no_base_snapshot({kind})"}
    base_date, base_path = base
    with open(base_path, encoding="utf-8") as fh:
        base_ds = json.load(fh)
    filings = None
    if molit_path and os.path.exists(molit_path):
        prev = load_molit_archive_on(base_date, 1, f"{snap_dir}/molit")
        if prev:
            with open(molit_path, encoding="utf-8") as fh:
                filings = diff_filings(prev, json.load(fh))
    weekly, weekly_rows = None, None
    if kind == "monthly":
        wb = resolve_base("weekly", today, snap_dir)
        if wb and wb[0] != base_date:
            with open(wb[1], encoding="utf-8") as fh:
                weekly = build_period_delta(ds, json.load(fh), "weekly", today, wb[0])
        weekly_rows = weekly_rows_between(base_date, today, snap_dir)
    macro_section = None
    if kind == "monthly" and macro_snapshot:
        from blog.macro_cycles import build_macro_regime_section
        try:
            macro_section = build_macro_regime_section(macro_snapshot, today)
        except Exception as e:                                   # noqa: BLE001 — L3 절 실패는 절 생략, 결산 발행 계속(S5 Codex)
            print(f"[period_delta] 거시 맥락 절 생략: {type(e).__name__}: {str(e)[:120]}")
    delta = build_period_delta(ds, base_ds, kind, today, base_date, filings=filings, weekly=weekly, weekly_rows=weekly_rows,
                               macro_section=macro_section)
    post = render_period_post(delta)
    label = POST_LABEL[kind]
    os.makedirs(f"{outdir}/posts", exist_ok=True)
    path = f"{outdir}/posts/{today}-{label}.html"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(post["html"])
    with open(f"{outdir}/posts/{today}-{label}.claims.jsonl", "w", encoding="utf-8") as fh:
        for c in post["claims"]:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    from blog.tistory_draft import write_digest_draft   # lazy — tistory_draft 는 top-level 에서 이미 import 되지만 관례 유지
    draft = write_digest_draft({"title": post["title"], "tags": post["tags"], "tistory_html": post["tistory_html"]},
                               today, outdir, kind="periodic")
    return {"kind": kind, "post": path, "draft": draft, "base_date": base_date.isoformat(), "n_pairs": delta["n_pairs"],
            "n_migrations": len(delta["migrations"]), "filings": bool(filings), "tistory_level": post["tistory_level"]}
