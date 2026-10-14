"""고유 통찰 연재 3종(2026-10-13, SPEC-D1 + SPEC-D2) — 같은 단지 넓혀가기 가격차(AreaUpsizeDiff)·보증금 변동 금액(DepositChangeAmount)·
예산 이하 체결 관측(BudgetTradeWatch).

정의 정본: report/2026-10-13-insight-series/SPEC-D1.md(결과 산출 전 동결, Codex·agy G0 반증 반영). 요지:
  · 공통 — 아파트만, 알려진 신원 문제(ambiguous_name·kapt_area_mismatch) 제외, (구, complex_no) 중복이면 실패, 원 단위로 계산하고
    표시 단계에서만 반올림, 결측·표본 부족은 0 이 아니라 '—'.
  · 넓혀가기 — K-apt 면적대 대조 통과 단지(역전 포함)의 12개월 59±3.5㎡ / 84±3.5㎡ 중위 차(이상치 컷 없음, 기존 AreaSpread 와 같음).
  · 보증금 — 발행 매매·전세 중위로 [가정] 보증금 s% 하락 시 돌려줄 금액. 비율 s·r/(1−r) 은 전세가율만의 함수라 본표는 금액.
  · 예산 — 80.5~87.5㎡ 원거래, 같은 길이 두 창(2026-01~04 vs 05~08, 9월은 신고 미완결이라 제외), 창 통과 n≥5 ∧ 계약일≥2,
    예산 이하 '서로 다른 계약일' 수로 0·1·2+ 분류한 3×3 교차표. 가격 컷 없음(이전 창 고정 컷은 민감도).
SPEC-D2(결과 확인 뒤 신원 문제 발견으로 추가, 전후 결과 모두 공개): 같은 면적대 거래를 정확 면적(소수 2자리)으로 묶었을 때
3건 이상 군집 두 개의 중위가 1.4배 이상 벌어지면 같은 구 동명 단지 병합 의심 → 세 연재 모두 보류(동작 대림 84.92㎡ 15~19억 vs
84.89㎡ 8~10억 실측). 연재 3 보조 지표는 단지 중위(대부분 0%라 퇴화) 대신 거래 합산 비율.
결정론·템플릿만 — 평가어·순위·전망·'갭'·'호가' 금지(wording_guard + 테스트). 발행·자동화와 연결하지 않는다(원고 생성까지).
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import statistics as st
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from decimal import ROUND_HALF_UP, Decimal
from html import escape
from pathlib import Path

import blog.build_explorer as be
from blog.build_explorer import SPLIT_RATIO, detect_area_price_split
from blog.build_site import BASE_URL
from blog.period_delta import _tbl
from blog.tistory_contract import DraftPayload, write_helper
from blog.tistory_draft import _MUT
from blog.wording_guard import assert_wording_ok

SPEC = "SPEC-D2"
SPEC_D3 = "SPEC-D3"   # 2026-10-14 고정 — D2 + 단지일련번호 복수 보류·직거래/저층 민감도·전세 신규 계약만(report/2026-10-14-series-automation/DESIGN.md §4)
FOLLOW_UP_DATE = "2026-11-10"
IDENTITY_BLOCKED = frozenset({"ambiguous_name", "kapt_area_mismatch"})
VERIFIED_FLAGS = frozenset({"", "total_inversion"})
YEAR_WINDOW = ("202510", "202609")

UPSIZE_ANCHORS_M2 = (59.0, 84.0)
UPSIZE_TOL_M2 = 3.5
UPSIZE_MIN_N = 5
UPSIZE_BANDS = ((6.0, "6억 미만"), (8.0, "6~8억"), (10.0, "8~10억"), (15.0, "10~15억"), (20.0, "15~20억"), (math.inf, "20억 이상"))
UPSIZE_RECENT = ("202604", "202609")
UPSIZE_NO_SEPT = ("202510", "202608")

DEPOSIT_SHOCKS = (0.05, 0.10, 0.20)
DEPOSIT_BANDS = ((40.0, "40% 미만"), (50.0, "40~50%"), (60.0, "50~60%"), (70.0, "60~70%"), (math.inf, "70% 이상"))

WATCH_AREA_M2 = (80.5, 87.5)
WATCH_BUDGET_WON = 1_000_000_000
WATCH_WINDOWS = (("202601", "202604"), ("202605", "202608"))
WATCH_ALT_WINDOWS = (("202603", "202605"), ("202606", "202608"))
WATCH_SEPT_WINDOWS = (("202602", "202605"), ("202606", "202609"))
WATCH_MIN_N = 5
WATCH_MIN_DAYS = 2
WATCH_REPEAT_DAYS = 2
WATCH_PRIOR_CUT = 0.6
WATCH_CLASSES = ("2+", "1", "0")

SEQ_COVERAGE_MIN = 0.99        # D3-1 적용 조건 — 기간 안 매칭 행 중 apt_seq 가 있는 비율
CONTRACT_COVERAGE_MIN = 0.90   # D3-3 적용 조건 — 매칭 전세 행 중 contract_type 이 있는 비율
LOW_FLOOR_MAX = 2              # D3-2 '1~2층 제외' 민감도
ROW_FILTERS = {"direct": "직거래 제외", "low_floor": "1~2층 제외"}
CONTRACT_TYPES = frozenset({"신규", "갱신"})   # D3-3 계약구분 적용 비율의 분자 — 그 밖의 값은 결측으로 센다


class SpecNotApplicable(ValueError):
    """SPEC-D3 를 이 수집본에 적용할 수 없음(새 수집 칸 결측) — 판을 보류한다."""


def shift_month(ym: str, k: int) -> str:
    """'YYYYMM' 을 k 개월 옮긴다(음수 = 과거)."""
    n = int(ym[:4]) * 12 + int(ym[4:]) - 1 + k
    return f"{n // 12}{n % 12 + 1:02d}"


@dataclass(frozen=True)
class SeriesWindows:
    """데이터 시계 M(수집본 마지막 계약월) 기준 이동 기간 — 10-13 판(M=202609)이면 종전 상수와 같다(DESIGN §3)."""
    last: str
    year: tuple[str, str]
    upsize_recent: tuple[str, str]
    upsize_no_last: tuple[str, str]
    watch: tuple[tuple[str, str], tuple[str, str]]
    watch_alt: tuple[tuple[str, str], tuple[str, str]]
    watch_last_incl: tuple[tuple[str, str], tuple[str, str]]


def windows_for(last: str) -> SeriesWindows:
    m = lambda k: shift_month(last, k)   # noqa: E731
    return SeriesWindows(last=last, year=(m(-11), last), upsize_recent=(m(-5), last), upsize_no_last=(m(-11), m(-1)),
                         watch=((m(-8), m(-5)), (m(-4), m(-1))), watch_alt=((m(-6), m(-4)), (m(-3), m(-1))),
                         watch_last_incl=((m(-7), m(-4)), (m(-3), last)))


DEFAULT_WINDOWS = windows_for(YEAR_WINDOW[1])

CAUTION_WATCH = ("※ 이 표는 국토교통부에 신고된 체결 거래만 셉니다. 최근 기간에 10억원 이하 거래가 없었다는 것은 지금 그 단지를 "
                 "10억원 이하로 살 수 없다는 뜻이 아니며, 10억원 이하 거래가 있었다는 것도 지금 그 가격에 살 수 있다는 뜻이 아닙니다. "
                 "층·동·향, 신고 시차(계약 후 30일 이내), 표본 크기에 따라 결과가 달라집니다.")


# ── 공통 ─────────────────────────────────────────────────────────────────

def months_between(start: str, end: str) -> list[str]:
    """'YYYYMM' 양끝 포함 월 목록."""
    y, m = int(start[:4]), int(start[4:])
    out = []
    while f"{y}{m:02d}" <= end:
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def require_complete_months(molit: dict, months: list[str]) -> None:
    """수집 완료 게이트 — 25구 × 사용 월이 `_done` 에 모두 있어야 한다. 누락 월을 0거래로 읽지 않는다."""
    done = set(molit.get("_done") or [])
    missing = [f"{lawd}|{ym}" for lawd in be.GU_LAWD.values() for ym in months if f"{lawd}|{ym}" not in done]
    if missing:
        raise ValueError(f"수집 미완료 구×월 {len(missing)}건: {missing[:5]}")


def select_panel_rows(ds: dict) -> list[dict]:
    """공통 패널 — 아파트, 알려진 신원 문제 제외. (구, complex_no) 중복이면 실패."""
    rows = [r for r in ds["complexes"]
            if r.get("product_type") == "아파트" and (r.get("spread_flag") or "") not in IDENTITY_BLOCKED]
    missing = [r.get("name") for r in rows if r.get("complex_no") in (None, "")]
    if missing:
        raise ValueError(f"complex_no 결측 {len(missing)}건: {missing[:3]} — 거래 색인 키가 겹친다")
    keys = Counter((r.get("gu"), str(r["complex_no"])) for r in rows)
    dup = [k for k, n in keys.items() if n > 1]
    if dup:
        raise ValueError(f"단지 키 중복 {len(dup)}건: {dup[:3]}")
    return rows


def index_named_trades(molit: dict) -> dict:
    """구 → (이름 색인, 원본명별 거래) — `_named_records_public` 과 같은 매칭(match_molit_names)을 구마다 한 번 색인해 재사용."""
    out = {}
    for gu, lawd in be.GU_LAWD.items():
        by_apt: dict[str, list[dict]] = defaultdict(list)
        for x in molit.get(lawd) or []:
            if isinstance(x, dict):
                by_apt[x.get("apt", "")].append(x)
        out[gu] = (be.build_name_index(by_apt.keys()), by_apt)
    return out


def _records(r: dict, trades_index: dict, months: list[str]) -> list[dict]:
    entry = trades_index.get(r["gu"])
    if not entry:
        return []
    idx, by_apt = entry
    ms = set(months)
    return [x for apt in sorted(be.match_molit_names(r["name"], (), idx)) for x in by_apt[apt]
            if (x.get("price") or 0) > 0 and x.get("ym") in ms]


def _band(value: float, bands) -> str:
    for hi, label in bands:
        if value < hi:
            return label
    return bands[-1][1]


def _q(vals: list[float], q: float) -> float:
    return be._pctile(sorted(vals), q)


def _identity(r: dict) -> str:
    return "확인" if (r.get("spread_flag") or "") in VERIFIED_FLAGS else "미확인"


def _won(eok: float | None) -> Decimal:
    """발행 억 단위 값(소수 2자리)을 원 단위 Decimal 로 — 이진 float 곱셈 오차 없이."""
    return Decimal(str(eok or 0)) * 100_000_000


def half_up(v: float | int, nd: int = 0) -> Decimal:
    """표시용 십진 HALF_UP 반올림 — float 의 최단 십진 표기(str)에서 출발해 이진 오차·은행가 반올림을 피한다(G2)."""
    return Decimal(str(v)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)


def summarize_data_clock(molit: dict) -> dict:
    """자료가 가리키는 시점 — 계약월별 서울 매매 원거래 행수와 마지막 계약월의 미완결 정도."""
    by = Counter(x["ym"] for k, v in molit.items() if k != "_done" and isinstance(v, list) for x in v if isinstance(x, dict))
    months = sorted(by)
    last = months[-1] if months else None
    full = [by[m] for m in months[:-1]]
    return {"rows_by_month": dict(sorted(by.items())), "first_month": months[0] if months else None, "last_month": last,
            "last_month_rows": by[last] if last else None,
            "earlier_month_median_rows": st.median(full) if full else None}


def _fkey(r: dict) -> tuple[str, str]:
    """고정 목록(지난 판) 대조 키 — complex_no 는 JSON 왕복에서 형이 바뀔 수 있어 문자열로."""
    return (r["gu"], str(r.get("complex_no")))


def _keep_row(x: dict, row_filter: str | None) -> bool:
    """D3-2 민감도 행 거르기 — direct: 직거래 제외, low_floor: 1~LOW_FLOOR_MAX층 제외(층 결측·0층·지하는 남긴다)."""
    if row_filter is None:
        return True
    if row_filter == "direct":
        return x.get("dealing") != "직거래"
    if row_filter == "low_floor":
        return not (x.get("floor") is not None and 1 <= x["floor"] <= LOW_FLOOR_MAX)
    raise ValueError(f"알 수 없는 row_filter {row_filter!r}")


def check_coverage(rows: list[dict], field: str, minimum: float, label: str, valid: frozenset | None = None) -> float | None:
    """D3 적용 조건(운영 지표) — 매칭 행 중 field 가 있는(valid 를 주면 그 값인) 비율이 minimum 미만이면 SpecNotApplicable.
    행이 없으면 None. 단지별 확인은 _seq_unverified 가 따로 한다."""
    if not rows:
        return None
    share = sum(1 for x in rows if (x.get(field) in valid if valid else x.get(field) not in (None, ""))) / len(rows)
    if share < minimum:
        raise SpecNotApplicable(f"{label}: {field} 있는 행 비율 {share:.4f} < {minimum} ({len(rows):,}행)")
    return share


def _seqs(recs: list[dict]) -> set:
    return {x["apt_seq"] for x in recs if x.get("apt_seq")}


def _seq_unverified(recs: list[dict]) -> bool:
    """D3-1 — 매칭 원거래에 단지일련번호(apt_seq)가 둘 이상이면 다른 단지 거래가 섞였고, 빈 행이 있으면 섞였는지 확인할 수 없다."""
    return len(_seqs(recs)) >= 2 or any(not x.get("apt_seq") for x in recs)


# ── 연재 1: 같은 단지 넓혀가기 가격차 ──────────────────────────────────────

def _upsize_one(r: dict, recs: list[dict], min_n: int) -> dict | None:
    lo, hi = UPSIZE_ANCHORS_M2
    a = [x["price"] for x in recs if abs(x["area"] - lo) <= UPSIZE_TOL_M2]
    b = [x["price"] for x in recs if abs(x["area"] - hi) <= UPSIZE_TOL_M2]
    if len(a) < min_n or len(b) < min_n:
        return None
    m59, m84 = st.median(a), st.median(b)
    return {"name": r["name"], "gu": r["gu"], "complex_no": r.get("complex_no"), "flag": r.get("spread_flag") or "",
            "n59": len(a), "n84": len(b), "med59_won": m59, "med84_won": m84, "diff_won": m84 - m59,
            "premium_pct": (m84 / m59 - 1) * 100, "band": _band(m59 / 1e8, UPSIZE_BANDS)}


def _upsize_stats(items: list[dict]) -> dict:
    if not items:
        return {"n": 0, "diff_median_eok": None, "diff_p25_eok": None, "diff_p75_eok": None, "premium_median_pct": None}
    d = [c["diff_won"] for c in items]   # 원 단위로 집계한 뒤 억으로(R2 N1 — 억 float 중위는 8.005→8.00499…)
    return {"n": len(items), "diff_median_eok": st.median(d) / 1e8, "diff_p25_eok": _q(d, 0.25) / 1e8,
            "diff_p75_eok": _q(d, 0.75) / 1e8,
            "premium_median_pct": st.median([c["premium_pct"] for c in items])}


def _sample_example(items: list[dict], n_keys: tuple[str, str]) -> dict | None:
    best = sorted(items, key=lambda c: (-min(c[n_keys[0]], c[n_keys[1]]), str(c["gu"]), str(c["name"])))
    return best[0] if best else None


def compute_area_upsize(ds: dict, molit: dict, min_n: int = UPSIZE_MIN_N, trades_index: dict | None = None,
                        win: SeriesWindows = DEFAULT_WINDOWS, spec: str = SPEC, only_keys: set | None = None) -> dict:
    """같은 단지 넓혀가기 가격차 — K-apt 대조 통과(역전 포함) 단지의 84㎡대 중위 − 59㎡대 중위(원, 12개월, 컷 없음).
    only_keys 를 주면 그 단지(_fkey)만 계산한다(지난 판 고정 목록 재계산). SPEC-D3 는 단지일련번호 복수 단지를 먼저 빼고
    직거래·1~2층 제외 민감도를 더한다."""
    year = months_between(*win.year)
    require_complete_months(molit, year)
    rows = [r for r in select_panel_rows(ds) if (r.get("spread_flag") or "") in VERIFIED_FLAGS]
    if only_keys is not None:
        rows = [r for r in rows if _fkey(r) in only_keys]
    ti = trades_index if trades_index is not None else index_named_trades(molit)
    recs = {(r["gu"], r.get("complex_no")): _records(r, ti, year) for r in rows}
    d3 = spec == SPEC_D3
    seq_keys: set = set()
    if d3:
        check_coverage([x for v in recs.values() for x in v], "apt_seq", SEQ_COVERAGE_MIN, "연재 1 매매")
        seq_keys = {k for k, v in recs.items() if _seq_unverified(v)}
    lo_a, hi_a = UPSIZE_ANCHORS_M2
    split_hold, seq_hold, items = [], [], []
    for r in rows:
        key = (r["gu"], r.get("complex_no"))
        c = _upsize_one(r, recs[key], min_n)
        if not c:
            continue
        if key in seq_keys:
            seq_hold.append({"name": c["name"], "gu": c["gu"], "complex_no": c["complex_no"]})
            continue
        if any(detect_area_price_split([x for x in recs[key] if abs(x["area"] - a) <= UPSIZE_TOL_M2]) for a in (lo_a, hi_a)):
            split_hold.append({"name": c["name"], "gu": c["gu"], "complex_no": c["complex_no"]})
            continue
        items.append(c)
    items.sort(key=lambda c: (c["gu"], c["name"]))
    split_hold.sort(key=lambda c: (c["gu"], c["name"]))
    seq_hold.sort(key=lambda c: (c["gu"], c["name"]))
    bands = []
    for _, label in UPSIZE_BANDS:
        sub = [c for c in items if c["band"] == label]
        bands.append({"band": label, **_upsize_stats(sub), "example": _sample_example(sub, ("n59", "n84"))})
    thresholds = []
    for k in (5, 10, 20):
        thresholds.append({"min_n": k, **_upsize_stats([c for c in items if c["n59"] >= k and c["n84"] >= k])})
    rows_by_key = {(r["gu"], r.get("complex_no")): r for r in rows}

    def _rerun(window: tuple[str, str] | None, row_filter: str | None = None) -> dict[tuple, dict]:
        ms = set(months_between(*window)) if window else None
        out = {}
        for c in items:
            key = (c["gu"], c["complex_no"])
            one = _upsize_one(rows_by_key[key], [x for x in recs[key] if (ms is None or x["ym"] in ms) and _keep_row(x, row_filter)],
                              min_n)
            if one:
                out[key] = one
        return out

    recent = _rerun(win.upsize_recent)
    same_set = [c for c in items if (c["gu"], c["complex_no"]) in recent]
    no_last = _rerun(win.upsize_no_last)
    nonpositive = [c for c in items if c["diff_won"] <= 0]
    out = {"spec": spec, "min_n": min_n, "window": list(win.year), "n_candidates": len(rows), **_upsize_stats(items),
           "bands": bands, "thresholds": thresholds,
           "same_set_recent": {"window": list(win.upsize_recent), "n": len(same_set),
                               "premium_median_pct_12m": st.median([c["premium_pct"] for c in same_set]) if same_set else None,
                               "premium_median_pct_recent": st.median([c["premium_pct"] for c in recent.values()]) if recent else None,
                               "diff_median_eok_12m": st.median([c["diff_won"] / 1e8 for c in same_set]) if same_set else None,
                               "diff_median_eok_recent": st.median([c["diff_won"] / 1e8 for c in recent.values()]) if recent else None},
           "no_last_month": {"window": list(win.upsize_no_last), **_upsize_stats(list(no_last.values()))},
           "n_nonpositive": len(nonpositive), "nonpositive": nonpositive, "split_hold": split_hold, "complexes": items}
    if d3:
        near = [x for c in items for x in recs[(c["gu"], c["complex_no"])]
                if any(abs(x["area"] - a) <= UPSIZE_TOL_M2 for a in UPSIZE_ANCHORS_M2)]
        out["sensitivity"] = [{"filter": f, "label": label, **_upsize_stats(list(_rerun(None, f).values())),
                               "n_rows_excluded": sum(1 for x in near if not _keep_row(x, f)),
                               **({"n_floor_missing": sum(1 for x in near if x.get("floor") is None)} if f == "low_floor" else {})}
                              for f, label in ROW_FILTERS.items()]
        out["seq_hold"] = seq_hold
    return out


# ── 연재 2: 보증금 변동 금액 ──────────────────────────────────────────────

def compute_deposit_change(ds: dict, shocks: tuple[float, ...] = DEPOSIT_SHOCKS, molit: dict | None = None,
                           trades_index: dict | None = None, win: SeriesWindows = DEFAULT_WINDOWS, spec: str = SPEC,
                           jeonse: dict | None = None, only_keys: set | None = None) -> dict:
    """보증금 변동 금액 — [가정] 보증금이 관측 전세 중위 J 에서 s 만큼 낮아질 때 돌려줄 금액 s×J(만원).
    매매·전세 중위 차이 D=S−J 대비 비율 s×J/D = s·r/(1−r) 은 전세가율 r 만의 함수(본표는 금액).
    molit 을 주면 대표면적 ±3.5㎡ 매매 원거래에 군집 분리 가드(SPEC-D2)를 건다 — 없으면 발행값만으로 계산(가드 없음).
    SPEC-D3: 전세 중위를 신규 계약 원거래(jeonse)만으로 사이트와 같은 방법(_match_records_public·_median_of)으로 다시 내고,
    매매·전세 매칭 행의 단지일련번호가 둘 이상이면 뺀다."""
    year = months_between(*win.year)
    if molit is not None:
        require_complete_months(molit, year)
    if spec == SPEC_D3 and jeonse is not None:
        require_complete_months(jeonse, year)
        ys = set(year)
        jeonse = {k: [x for x in v if isinstance(x, dict) and x.get("ym") in ys] for k, v in jeonse.items()
                  if k != "_done" and isinstance(v, list)}
    ti = (trades_index if trades_index is not None else index_named_trades(molit)) if molit is not None else None
    d3 = spec == SPEC_D3
    rows = select_panel_rows(ds)
    if only_keys is not None:
        rows = [r for r in rows if _fkey(r) in only_keys]
    jmatch: dict = {}
    sale_recs: dict = {}
    n_renewal = 0
    if d3:
        if ti is None or jeonse is None:
            raise ValueError("SPEC-D3 연재 2 는 매매·전세 원거래가 모두 필요하다")
        jeonse_new = {k: [x for x in v if isinstance(x, dict) and x.get("contract_type") == "신규"]
                      for k, v in jeonse.items() if k != "_done" and isinstance(v, list)}
        for r in rows:
            lawd = be.GU_LAWD.get(r["gu"])
            if lawd and r.get("area_m2"):
                jmatch[_fkey(r)] = be._match_records_public(r["name"], float(r["area_m2"]), lawd, jeonse)
            sale_recs[_fkey(r)] = _records(r, ti, year)
        all_j = [x for v in jmatch.values() for x in v]
        check_coverage(all_j, "contract_type", CONTRACT_COVERAGE_MIN, "연재 2 전세", valid=CONTRACT_TYPES)
        check_coverage(all_j, "apt_seq", SEQ_COVERAGE_MIN, "연재 2 전세")
        check_coverage([x for v in sale_recs.values() for x in v], "apt_seq", SEQ_COVERAGE_MIN, "연재 2 매매")
    items, split_hold, seq_hold = [], [], []
    for r in rows:
        if d3:
            lawd = be.GU_LAWD.get(r["gu"])
            jm, jn = (be._median_of(be._match_records_public(r["name"], float(r["area_m2"]), lawd, jeonse_new))
                      if lawd and r.get("area_m2") else (None, 0))
            s_eok = r.get("molit_recent_eok")
            r = {**r, "jeonse_recent_eok": jm, "jeonse_n": jn,
                 "jeonse_ratio_complex_pct": round(jm / s_eok * 100, 1) if jm is not None and s_eok else None}
        if not be.passes_jeonse_gate(r) or r["jeonse_ratio_complex_pct"] < 0:
            continue
        s_won, j_won = _won(r.get("molit_recent_eok")), _won(r.get("jeonse_recent_eok"))
        if s_won <= 0 or j_won <= 0 or s_won <= j_won:
            continue
        if d3:
            n_renewal += sum(1 for x in jmatch.get(_fkey(r), []) if x.get("contract_type") == "갱신")
            sr, jr = sale_recs.get(_fkey(r), []), jmatch.get(_fkey(r), [])
            if _seq_unverified(sr) or _seq_unverified(jr) or (sr and jr and _seqs(sr) != _seqs(jr)):   # 매매·전세가 같은 단지인지
                seq_hold.append([r["gu"], r["name"]])
                continue
        if ti is not None and detect_area_price_split(
                [x for x in _records(r, ti, year) if abs(x["area"] - float(r.get("area_m2") or 0)) <= UPSIZE_TOL_M2]):
            split_hold.append([r["gu"], r["name"]])
            continue
        d_won = s_won - j_won
        ratio = j_won / s_won * 100   # Decimal — 발행 전세가율(소수 1자리)·float 경계 오차 없이 구간 판정(G2·G3)
        items.append({"name": r["name"], "gu": r["gu"], "complex_no": r.get("complex_no"), "area_m2": r.get("area_m2"),
                      "flag": r.get("spread_flag") or "", "sale_eok": r["molit_recent_eok"], "sale_n": r.get("molit_n"),
                      "jeonse_eok": r["jeonse_recent_eok"], "jeonse_n": r.get("jeonse_n"),
                      "jeonse_ratio_pct": float(ratio), "diff_won": int(d_won), "diff_eok": float(d_won / 100_000_000),
                      "band": _band(ratio, DEPOSIT_BANDS),
                      "amount_man": {s: float(Decimal(str(s)) * j_won / 10_000) for s in shocks},
                      "ratio_pct": {s: float(Decimal(str(s)) * j_won / d_won * 100) for s in shocks}})
    items.sort(key=lambda c: (c["gu"], c["name"], c["area_m2"] or 0))

    def _stats(sub: list[dict]) -> dict:
        if not sub:
            return {"n": 0, "amount10_median_man": None, "amount10_p25_man": None, "amount10_p75_man": None,
                    "diff_median_eok": None, "ratio10_median_pct": None,
                    "amount_median_man": {s: None for s in shocks}}
        a10 = [c["amount_man"][0.10] for c in sub] if 0.10 in shocks else []
        return {"n": len(sub),
                "amount10_median_man": int(half_up(st.median(a10))) if a10 else None,
                "amount10_p25_man": int(half_up(_q(a10, 0.25))) if a10 else None,
                "amount10_p75_man": int(half_up(_q(a10, 0.75))) if a10 else None,
                "diff_median_eok": st.median([c["diff_won"] for c in sub]) / 1e8,
                "ratio10_median_pct": st.median([c["ratio_pct"][0.10] for c in sub]) if 0.10 in shocks else None,
                "amount_median_man": {s: int(half_up(st.median([c["amount_man"][s] for c in sub]))) for s in shocks}}

    bands = []
    for _, label in DEPOSIT_BANDS:
        sub = [c for c in items if c["band"] == label]
        bands.append({"band": label, **_stats(sub), "example": _sample_example(sub, ("sale_n", "jeonse_n"))})
    out = {"spec": spec, "shocks": list(shocks), **_stats(items), "bands": bands,
           "n_identity_unverified": sum(1 for c in items if c["flag"] not in VERIFIED_FLAGS),
           "split_guard": molit is not None, "n_split_hold": len(split_hold), "split_hold": sorted(split_hold), "complexes": items}
    if d3:
        out.update({"seq_hold": sorted(seq_hold), "n_renewal_rows_excluded": n_renewal})
    return out


# ── 연재 3: 예산 이하 체결 관측 ───────────────────────────────────────────

def classify_window(trades: list[dict], budget_won: int, min_n: int = WATCH_MIN_N, min_days: int = WATCH_MIN_DAYS,
                    repeat_days: int = WATCH_REPEAT_DAYS) -> str:
    """창 분류 — 'short'(n<min_n 또는 계약일<min_days: 0 이 아님) / '0' / '1'(예산 이하 계약일 1~repeat_days−1) /
    '2+'(예산 이하 서로 다른 계약일 ≥ repeat_days). 같은 날 여러 행은 한 계약일로 센다(계약 ID 없음)."""
    if len(trades) < min_n or len({x["deal_date"] for x in trades}) < min_days:
        return "short"
    d_le = len({x["deal_date"] for x in trades if x["price"] <= budget_won})
    return "0" if d_le == 0 else "2+" if d_le >= repeat_days else "1"


def _shared_source_keys(rows: list[dict], recs: dict) -> set:
    """두 패널 단지가 같은 MOLIT 원본명(구 안)을 공유하면 둘 다 제외 — 같은 거래를 두 번 세지 않는다."""
    owners: dict[tuple, set] = defaultdict(set)
    for r in rows:
        key = (r["gu"], r.get("complex_no"))
        for x in recs[key]:
            owners[(r["gu"], x["apt"])].add(key)
    return {k for keys in owners.values() if len(keys) > 1 for k in keys}


def _window_detail(trades: list[dict], budget_won: int) -> dict:
    le = [x for x in trades if x["price"] <= budget_won]
    return {"n": len(trades), "n_le": len(le), "days_le": len({x["deal_date"] for x in le}),
            "med_eok": float(half_up(Decimal(str(st.median(x["price"] for x in trades))) / 100_000_000, 2)) if trades else None,
            "share_le_pct": len(le) / len(trades) * 100 if trades else None}


def compute_budget_watch(ds: dict, molit: dict, budget_won: int = WATCH_BUDGET_WON,
                         windows: tuple | None = None, min_n: int = WATCH_MIN_N, min_days: int = WATCH_MIN_DAYS,
                         repeat_days: int = WATCH_REPEAT_DAYS, area: tuple[float, float] = WATCH_AREA_M2,
                         verified_only: bool = False, prior_cut: float | None = None, trades_index: dict | None = None,
                         split_guard: bool = True, win: SeriesWindows = DEFAULT_WINDOWS, spec: str = SPEC,
                         row_filter: str | None = None, only_keys: set | None = None) -> dict:
    """예산 이하 체결 관측 — 고정 패널 단지의 area 범위 매매 원거래를 같은 길이 두 창으로 나눠, 창마다 예산 이하
    서로 다른 계약일 수로 분류한 3×3 교차표. prior_cut 은 이전 창 중위 × prior_cut 미만 거래를 두 창 모두에서 뺀다(민감도).
    windows 가 없으면 win.watch(이동 주 창). SPEC-D3 는 12개월 매칭 행의 단지일련번호가 둘 이상인 단지를 빼고(seq_hold),
    row_filter(직거래·1~2층 제외)는 두 창 거래에 건다. only_keys 는 지난 판 고정 패널(_fkey)."""
    windows = windows or win.watch
    ma, mb = months_between(*windows[0]), months_between(*windows[1])
    year = months_between(*win.year)
    d3 = spec == SPEC_D3
    require_complete_months(molit, sorted(set(ma + mb + (year if split_guard or d3 else []))))   # 군집·일련번호 판정은 12개월(G3)
    rows = select_panel_rows(ds)
    if verified_only:
        rows = [r for r in rows if (r.get("spread_flag") or "") in VERIFIED_FLAGS]
    if only_keys is not None:
        rows = [r for r in rows if _fkey(r) in only_keys]
    lo, hi = area
    ti = trades_index if trades_index is not None else index_named_trades(molit)
    recs = {(r["gu"], r.get("complex_no")): [x for x in _records(r, ti, ma + mb) if lo <= x["area"] <= hi and _keep_row(x, row_filter)]
            for r in rows}
    shared = _shared_source_keys(rows, recs)
    year_recs = {(r["gu"], r.get("complex_no")): _records(r, ti, year) for r in rows} if (split_guard or d3) else {}
    seq: set = set()
    if d3:
        check_coverage([x for v in year_recs.values() for x in v], "apt_seq", SEQ_COVERAGE_MIN, "연재 3 매매")
        seq = {k for k, v in year_recs.items() if _seq_unverified(v)} - shared
    split = ({k for k, v in year_recs.items() if detect_area_price_split([x for x in v if lo <= x["area"] <= hi])} - seq
             if split_guard else set())
    cross = {f"{a}|{b}": 0 for a in WATCH_CLASSES for b in WATCH_CLASSES}
    cells: dict[str, list] = {k: [] for k in cross}
    short = {"A": 0, "B": 0, "both": 0, "no_trades": 0}
    by_gu: dict[str, Counter] = defaultdict(Counter)
    status: dict[tuple, str] = {}
    b_le: dict[tuple, int] = {}   # B창 예산 이하 서로 다른 계약일 — 창 통과와 별개(지난 판 '최근 없음' 단지의 획득 판정)
    for r in rows:
        key = (r["gu"], r.get("complex_no"))
        by_gu[r["gu"]]["panel"] += 1
        if key in shared or key in seq or key in split:
            status[key] = "shared_source" if key in shared else "seq_hold" if key in seq else "split_hold"
            continue
        ta = [x for x in recs[key] if x["ym"] in ma]
        tb = [x for x in recs[key] if x["ym"] in mb]
        if not ta and not tb:
            short["no_trades"] += 1
            status[key] = "no_trades"
            b_le[key] = 0
            continue
        if prior_cut is not None and ta:
            floor_won = st.median(x["price"] for x in ta) * prior_cut
            ta = [x for x in ta if x["price"] >= floor_won]
            tb = [x for x in tb if x["price"] >= floor_won]
        b_le[key] = len({x["deal_date"] for x in tb if x["price"] <= budget_won})
        ca = classify_window(ta, budget_won, min_n, min_days, repeat_days)
        cb = classify_window(tb, budget_won, min_n, min_days, repeat_days)
        if ca == "short" or cb == "short":
            kind = "both" if ca == cb else "A" if ca == "short" else "B"
            short[kind] += 1
            status[key] = f"short_{kind}"
            continue
        cell = f"{ca}|{cb}"
        status[key] = cell
        cross[cell] += 1
        by_gu[r["gu"]]["pass"] += 1
        by_gu[r["gu"]][cell] += 1
        da, db = _window_detail(ta, budget_won), _window_detail(tb, budget_won)
        cells[cell].append({"name": r["name"], "gu": r["gu"], "complex_no": r.get("complex_no"), "identity": _identity(r),
                            **{f: {"A": da[f], "B": db[f]} for f in ("n", "n_le", "days_le", "med_eok", "share_le_pct")}})
    for v in cells.values():
        v.sort(key=lambda c: (c["gu"], c["name"]))
    passed = [c for v in cells.values() for c in v]
    params = {"budget_won": budget_won, "windows": [list(w) for w in windows], "min_n": min_n, "min_days": min_days,
              "repeat_days": repeat_days, "area_m2": list(area), "verified_only": verified_only, "prior_cut": prior_cut,
              "split_guard": split_guard}
    if row_filter:
        params["row_filter"] = row_filter
        params["n_rows_excluded"] = sum(1 for r in rows for x in _records(r, ti, ma + mb)
                                        if lo <= x["area"] <= hi and not _keep_row(x, row_filter))
    extra = {"n_seq_hold": len(seq), "seq_hold": sorted([r["gu"], r["name"]] for r in rows if (r["gu"], r.get("complex_no")) in seq)} if d3 else {}
    return {"spec": spec, "params": params, **extra,
            "n_panel": len(rows), "n_shared_source": len(shared), "n_split_hold": len(split - shared),
            "split_hold": sorted(([r["gu"], r["name"]] for r in rows if (r["gu"], r.get("complex_no")) in split - shared)),
            "n_pass_both": sum(cross.values()), "short": short,
            "cross": cross, "cells": cells,
            "share_le_median_pct": {w: st.median([c["share_le_pct"][w] for c in passed]) if passed else None for w in ("A", "B")},
            "share_le_pooled_pct": {w: (sum(c["n_le"][w] for c in passed) / sum(c["n"][w] for c in passed) * 100) if passed else None
                                    for w in ("A", "B")},
            "n_trades": {w: sum(c["n"][w] for c in passed) for w in ("A", "B")},
            "panel": [{"gu": r["gu"], "complex_no": r.get("complex_no"), "name": r["name"], "identity": _identity(r),
                       "status": status[(r["gu"], r.get("complex_no"))], "b_le_days": b_le.get((r["gu"], r.get("complex_no")))}
                      for r in rows],
            "by_gu": [{"gu": gu, **{k: c.get(k, 0) for k in ("panel", "pass", "2+|2+", "2+|0", "0|2+")}}
                      for gu, c in sorted(by_gu.items())]}


def _grid_row(label: str, res: dict) -> dict:
    return {"setting": label, "params": res["params"], "n_pass_both": res["n_pass_both"], "cross": res["cross"]}


def compute_budget_watch_grid(ds: dict, molit: dict, trades_index: dict | None = None,
                              win: SeriesWindows = DEFAULT_WINDOWS, spec: str = SPEC) -> list[dict]:
    """민감도 격자(SPEC-D1 순서 고정, 전수) — 주설정 → 예산 9/10/11억 × 반복 2/3계약일 × 최소 5/3건 → 창·신원·컷 대안.
    SPEC-D3 는 끝에 직거래·1~2층 제외 줄을 더한다(앞 줄 순서는 그대로)."""
    ti = trades_index if trades_index is not None else index_named_trades(molit)
    kw = {"trades_index": ti, "win": win, "spec": spec}
    out = [_grid_row("주설정", compute_budget_watch(ds, molit, **kw))]
    for eok in (9, 10, 11):
        for rep in (2, 3):
            for mn in (5, 3):
                if (eok, rep, mn) == (10, WATCH_REPEAT_DAYS, WATCH_MIN_N):
                    continue
                res = compute_budget_watch(ds, molit, budget_won=eok * 100_000_000, repeat_days=rep, min_n=mn, **kw)
                out.append(_grid_row(f"예산 {eok}억 · 반복 {rep}계약일 · 창 최소 {mn}건", res))
    alt, incl = win.watch_alt, win.watch_last_incl
    out.append(_grid_row(f"창 3개월씩({_wlabel(alt[0])} vs {_wlabel(alt[1])})", compute_budget_watch(ds, molit, windows=alt, **kw)))
    out.append(_grid_row(f"{_mlabel(win.last)} 포함 참고({_wlabel(incl[0])} vs {_wlabel(incl[1])})",
                         compute_budget_watch(ds, molit, windows=incl, **kw)))
    out.append(_grid_row("신원 확인 단지만", compute_budget_watch(ds, molit, verified_only=True, **kw)))
    out.append(_grid_row("이전 창 중위 60% 미만 제외", compute_budget_watch(ds, molit, prior_cut=WATCH_PRIOR_CUT, **kw)))
    out.append(_grid_row("가격 군집 분리 보류 없음(SPEC-D1)", compute_budget_watch(ds, molit, split_guard=False, **kw)))
    if spec == SPEC_D3:
        for f, label in ROW_FILTERS.items():
            out.append(_grid_row(label, compute_budget_watch(ds, molit, row_filter=f, **kw)))
    return out


SERIES_KINDS = ("upsize", "deposit", "watch")


def build_series_results(ds: dict, molit: dict, win: SeriesWindows = DEFAULT_WINDOWS, spec: str = SPEC,
                         jeonse: dict | None = None, kinds: tuple[str, ...] = SERIES_KINDS) -> dict:
    """연재 결과 + 민감도 + 자료 시점 — 같은 입력이면 같은 결과(재현성). kinds 로 필요한 연재만 계산한다."""
    ti = index_named_trades(molit)
    out = {"spec": spec, "asof": ds.get("data_asof"), "follow_up": FOLLOW_UP_DATE, "takedown": ds.get("takedown"),
           "data_clock": summarize_data_clock(molit), "windows": asdict(win)}
    if "upsize" in kinds:
        out["upsize"] = compute_area_upsize(ds, molit, trades_index=ti, win=win, spec=spec)
    if "deposit" in kinds:
        out["deposit"] = compute_deposit_change(ds, molit=molit, trades_index=ti, win=win, spec=spec, jeonse=jeonse)
    if "watch" in kinds:
        out["watch"] = compute_budget_watch(ds, molit, trades_index=ti, win=win, spec=spec)
        out["watch_grid"] = compute_budget_watch_grid(ds, molit, trades_index=ti, win=win, spec=spec)
    return out


# ── 원고(티스토리 본문) ───────────────────────────────────────────────────

def _eok_txt(v: float | None, nd: int = 2) -> str:
    return "—" if v is None else f"{half_up(v, nd):,.{nd}f}억원"


def _man_txt(v: float | None) -> str:
    return "—" if v is None else f"{half_up(v, 0):,.0f}만원"


def _pct_txt(v: float | None, signed: bool = True) -> str:
    if v is None:
        return "—"
    d = half_up(v, 1)
    return f"{d:+.1f}%" if signed else f"{d:.1f}%"


def _ym_txt(ym: str | None) -> str:
    return "—" if not ym else f"{int(ym[:4])}년 {int(ym[4:])}월"


def _mlabel(ym: str) -> str:
    """월만 — '9월'(앞뒤 문맥이 연도를 정할 때)."""
    return f"{int(ym[4:])}월"


def _h3(text: str) -> str:
    return f'<h3 style="font-size:16px;font-weight:bold;margin:22px 0 8px">{text}</h3>'


def _p(text: str, mut: bool = False) -> str:
    return f'<p style="{_MUT}">{text}</p>' if mut else f"<p>{text}</p>"


def _box(text: str) -> str:
    return f'<p style="background:#f8fafc;border-left:4px solid #64748b;padding:10px 14px;margin:12px 0">{text}</p>'


def _question(q: str, answer: str) -> str:
    return (f'<p style="background:#f0f7ff;border-left:4px solid #0969da;padding:10px 14px;margin:12px 0">'
            f'<b>독자의 질문</b> — {q}<br><b>[관측]</b> {answer}</p>')


def _site_link(path: str, label: str) -> str:
    return f'<a href="{BASE_URL}/{path}">{label}</a>'


def _clock_section(clock: dict, window_txt: str, window_end: str | None = None) -> str:
    """window_end 가 수집본 마지막 달보다 앞이면(연재 3) 마지막 달을 뺀 이유로, 아니면 덜 들어온 달이 포함됐다는 고지로 쓴다(G3)."""
    last = clock.get("last_month")
    if not last:
        return _h3("자료가 가리키는 시점") + _p(f"가격은 계약월 기준 {window_txt} 거래입니다. 이번 수집본에는 해당 기간 계약이 없습니다(—). "
                                         "오늘 확인한 거래가 오늘의 시장을 뜻하지 않습니다.")
    counts = (f"{_ym_txt(last)} 계약 {clock.get('last_month_rows') or 0:,}건, 그 전 달들의 중위 "
              f"{clock.get('earlier_month_median_rows') or 0:,.0f}건")
    if window_end and last and window_end < last:
        lead = (f"가격은 계약월 기준 {window_txt} 거래입니다. 신고는 계약 후 30일 안에 하므로 수집본의 마지막 달 계약은 아직 덜 들어옵니다({counts}). "
                f"그래서 이 글의 주비교는 {_ym_txt(window_end)} 계약에서 끝냈고, 마지막 달까지 넣은 결과는 설정표의 참고 줄에만 있습니다. ")
    else:
        lead = f"가격은 계약월 기준 {window_txt} 거래입니다. 신고는 계약 후 30일 안에 하므로 마지막 달 계약은 아직 덜 들어옵니다({counts}). "
    return _h3("자료가 가리키는 시점") + _p(lead + "오늘 확인한 거래가 오늘의 시장을 뜻하지 않습니다.")


def _method_section(asof: str, extra: str, takedown: str) -> str:
    return (f'<hr><p style="{_MUT}"><b>방법·출처</b><br>'
            f"• 국토교통부 아파트 매매·전월세 실거래(RTMS) 공공데이터, 자료 기준일 {escape(asof)}. 해제 표시 거래 제외.<br>"
            f"• {extra}<br>"
            "• 중위 = 거래 가격을 줄 세웠을 때 가운데 값. 아주 높거나 낮은 몇 건에 평균보다 덜 흔들립니다.<br>"
            "• 자체 점수·순위를 만들지 않습니다. 같은 정의로 다시 계산할 수 있도록 정의와 표본 수를 함께 적습니다.<br>"
            f"• {_site_link('methodology.html', '방법론 전문')} · {_site_link('explorer.html', '단지 탐색기')}</p>"
            f'<p style="{_MUT}">⚖ {be.DISCLAIMER} {escape(takedown)}</p>')


def _check(text: str, where: str) -> str:
    assert_wording_ok(text, where)
    for banned in ("갭", "자기자본", "사다리", "매수 가능"):
        if banned in text:
            raise ValueError(f"[insight-series] 금지어 '{banned}' in {where}")
    return text


_WORD_JOINER = "\u2060"


def _bind_units(html_text: str) -> str:
    """숫자와 ㎡ 사이에 WORD JOINER(U+2060)를 넣어 좁은 표 칸에서 '59'와 '㎡대'가 줄바꿈으로 갈리지 않게 한다.
    ㎡(U+33A1)는 줄바꿈 등급이 표의문자라 keep-all 에서도 숫자 뒤가 끊긴다(G5 미리보기 실측). 카카오 에디터가
    span 스타일(nowrap)을 지우므로 문자로 막는다."""
    return re.sub(r"(\d)㎡", lambda m: m.group(1) + _WORD_JOINER + "㎡", html_text)


def _split_txt(hold: list) -> str:
    """같은 면적대 가격 군집 분리(SPEC-D2) 보류 단지 문장 — [구, 이름] 또는 {gu, name} 목록."""
    if not hold:
        return "같은 면적대 안에서 거래 가격이 두 무리로 갈린 단지(동명 단지 섞임 의심)는 없었습니다."
    names = ", ".join(f'{escape(h["name"] if isinstance(h, dict) else h[1])}({escape(h["gu"] if isinstance(h, dict) else h[0])})' for h in hold)
    return (f"같은 면적대 거래를 면적 소수점 둘째 자리까지 같은 것끼리 묶었을 때 가격이 {SPLIT_RATIO:g}배 이상 갈린 {len(hold)}곳({names})은 "
            "같은 구의 이름이 같은 다른 단지 거래가 섞였을 수 있어 계산에서 뺐습니다.")


def _trend_txt(filled: list[dict]) -> str:
    """가격대별 가격차 중위가 단조 증가인지 결과로 판정해 문장을 고른다(예외 구간은 이름으로)."""
    if len(filled) < 2:
        return "가격대가 둘 이상 채워지지 않아 가격대별 비교는 하지 않았습니다. 또"
    dips = [(p, c) for p, c in zip(filled, filled[1:]) if c["diff_median_eok"] <= p["diff_median_eok"]]
    if not dips:
        return "금액으로 본 넓혀가기 가격차는 59㎡대 가격대가 높을수록 커졌습니다. 반면"
    ex = ", ".join(f'{p["band"]} {_eok_txt(p["diff_median_eok"])}·{c["band"]} {_eok_txt(c["diff_median_eok"])}' for p, c in dips)
    return f"금액으로 본 넓혀가기 가격차는 가격대가 높을수록 대체로 커졌지만 이웃 구간이 거의 같은 곳도 있습니다({ex}). 또"


def _ratio_order_txt(filled: list[dict], prem_sorted: list[dict]) -> str:
    """59㎡대 대비 비율이 가격대 순서와 같은 방향인지 결과로 판정해 문장을 고른다(자동판 — 고정 결론 금지)."""
    span = (f"59㎡대 대비 비율은 {prem_sorted[0]['band']} 구간 {_pct_txt(prem_sorted[0]['premium_median_pct'])}에서 "
            f"{prem_sorted[-1]['band']} 구간 {_pct_txt(prem_sorted[-1]['premium_median_pct'])} 사이로, ")
    p = [b["premium_median_pct"] for b in filled]
    if len(p) < 2:
        return "59㎡대 대비 비율은 채워진 가격대가 하나뿐이라 가격대 순서와 비교하지 않았습니다."
    if all(a < b for a, b in zip(p, p[1:])):
        return span + "가격대가 높을수록 커졌습니다. 그래도 구간마다 단지 수가 달라 비율 하나를 모든 가격대에 곱해 쓰기는 어렵습니다."
    if all(a > b for a, b in zip(p, p[1:])):
        return span + "가격대가 높을수록 작아졌습니다. 그래도 구간마다 단지 수가 달라 비율 하나를 모든 가격대에 곱해 쓰기는 어렵습니다."
    return span + "가격대 순서와 같은 방향으로 움직이지 않습니다. 비율 하나를 모든 가격대에 곱해 쓰기는 어렵습니다."


def _seq_txt(hold: list | None, pair: bool = False) -> str:
    """D3-1 단지일련번호 보류 문장 — D2 판(None)이면 빈 문자열. pair=연재 2(매매·전세 값 일치까지 확인)."""
    if hold is None:
        return ""
    what = ("국토교통부 단지일련번호 값이 둘 이상 섞였거나 비어 있거나 매매·전세 값이 서로 다른" if pair
            else "국토교통부 단지일련번호 값이 둘 이상 섞였거나 비어 있는")
    limit = " 단지일련번호 값이 하나인 단지도 사이트 단지와 같은 단지인지는 주소로 맞춰 보지 않았습니다(사이트 자료에 대조할 주소가 없음)."
    if not hold:
        return f" 매칭된 거래에 {what} 단지는 없었습니다." + limit
    names = [f'{escape(h["name"] if isinstance(h, dict) else h[1])}({escape(h["gu"] if isinstance(h, dict) else h[0])})' for h in hold]
    shown = ", ".join(names[:10]) + (f" 외 {len(names) - 10}곳" if len(names) > 10 else "")
    return f" 매칭된 거래에 {what} {len(hold)}곳({shown})은 다른 단지 거래가 섞였는지 확인할 수 없어 뺐습니다." + limit


def _edition_section(edition: dict | None) -> str:
    """'지난 판과 비교' — 판정·이유·지난 판 링크·(지난 판 → 같은 정의·새 자료 → 이번 판) 표. 10-13 첫 판(None)은 생략."""
    if not edition:
        return ""
    prev = (f'<a href="{escape(edition["prev_url"])}">{escape(edition["prev_day"])} 판</a>' if edition.get("prev_url")
            else f'{escape(edition["prev_day"])} 판')
    reasons = " ".join(escape(r) for r in edition["reasons"])
    out = (_h3("[관측] 지난 판과 비교")
           + _p(f"지난 판: {prev}. 지난 판의 해석에 대한 판정: <b>{escape(edition['verdict'])}</b> — {reasons}")
           + _tbl(edition["headers"], edition["rows"], True))
    for note in edition.get("notes", []):
        out += _p(escape(note), True)
    return out


def _followup_txt(edition: dict | None, legacy: str) -> str:
    if not edition:
        return legacy
    return (f"다음 판은 {edition['next_date']}에 같은 정의로 냅니다. 이번 판의 단지 목록을 고정해 두고, 단지 구성이 바뀐 몫과 "
            "값이 바뀐 몫을 나눠 적은 뒤 이번 해석을 유지·수정·보류 중 하나로 밝힙니다.")


def render_upsize_post(res: dict, clock: dict, asof: str, today: str, takedown: str,
                       win: SeriesWindows = DEFAULT_WINDOWS, edition: dict | None = None) -> DraftPayload:
    u = res
    d3 = u.get("spec") == SPEC_D3
    ex_rows = []
    for b in u["bands"]:
        if not b["n"]:
            ex_rows.append([b["band"], "기준 충족 단지 없음", "—", "—", "—"])
            continue
        e = b["example"]
        ex_rows.append([b["band"], f'{b["n"]}곳',
                        f'{_eok_txt(b["diff_median_eok"])} (중간 50%: {_eok_txt(b["diff_p25_eok"])}~{_eok_txt(b["diff_p75_eok"])})',
                        _pct_txt(b["premium_median_pct"]),
                        f'{escape(e["name"])}({escape(e["gu"])}) 59㎡대 {_eok_txt(e["med59_won"] / 1e8)}·{e["n59"]}건 → '
                        f'84㎡대 {_eok_txt(e["med84_won"] / 1e8)}·{e["n84"]}건'])
    table_a = _tbl(["59㎡대 중위 가격대", "단지 수", "84㎡대 − 59㎡대 중위", "59㎡대 대비", "표본 많은 예시(대표값 아님)"], ex_rows, True)
    th_rows = [[f'양쪽 각 {t["min_n"]}건 이상', f'{t["n"]}곳', _eok_txt(t["diff_median_eok"]), _pct_txt(t["premium_median_pct"])]
               for t in u["thresholds"]]
    ss, ns = u["same_set_recent"], u["no_last_month"]
    th_rows.append([f'최근 6개월에도 두 면적 각 5건 이상인 {ss["n"]}곳(12개월 → 6개월)', f'{ss["n"]}곳',
                    f'{_eok_txt(ss["diff_median_eok_12m"])} → {_eok_txt(ss["diff_median_eok_recent"])}',
                    f'{_pct_txt(ss["premium_median_pct_12m"])} → {_pct_txt(ss["premium_median_pct_recent"])}'])
    th_rows.append([f"{_mlabel(win.last)} 계약 제외(11개월)", f'{ns["n"]}곳', _eok_txt(ns["diff_median_eok"]), _pct_txt(ns["premium_median_pct"])])
    for e in u.get("sensitivity", []):
        th_rows.append([e["label"] + f' (제외 {e.get("n_rows_excluded", 0):,}건'
                        + (f', 층 정보 없는 행 {e["n_floor_missing"]:,}건은 남김)' if "n_floor_missing" in e else ")"),
                        f'{e["n"]}곳', _eok_txt(e["diff_median_eok"]), _pct_txt(e["premium_median_pct"])])
    table_b = _tbl(["비교 기준", "단지 수", "가격차 중위", "59㎡대 대비 중위"], th_rows, True)
    nonpos = u["nonpositive"]
    nonpos_txt = (", ".join(f'{escape(c["name"])}({escape(c["gu"])}) {_eok_txt(c["diff_won"] / 1e8)}' for c in nonpos)
                  if nonpos else "없음")
    filled = [b for b in u["bands"] if b["n"]]
    lo_b, hi_b = (filled[0], filled[-1]) if filled else ({"band": "—", "diff_median_eok": None},) * 2
    prem_sorted = sorted(filled, key=lambda b: b["premium_median_pct"]) or [{"band": "—", "premium_median_pct": None}]
    q = "같은 단지 안에서 59㎡대와 84㎡대의 실거래 중위 가격은 얼마나 차이 났을까?"
    a = (f'같은 단지 안에서 59㎡대와 84㎡대가 최근 12개월 각각 5건 이상 거래된 {u["n"]}곳을 보면, 84㎡대 중위가 59㎡대 중위보다 '
         f'단지 중위로 {_eok_txt(u["diff_median_eok"])} 높았습니다(59㎡대 대비 {_pct_txt(u["premium_median_pct"])}). '
         f'59㎡대 가격대별 가격차 중위는 {lo_b["band"]} 구간 {_eok_txt(lo_b["diff_median_eok"])}, {hi_b["band"]} 구간 {_eok_txt(hi_b["diff_median_eok"])}이었습니다.')
    body = (
        _question(q, a)
        + _h3("[관측] 59㎡대 가격대별로 본 넓혀가기 가격차")
        + _p(f"대상: K-apt 면적대 세대수 대조로 단지 신원을 확인한 아파트 중 59㎡대(±3.5㎡)·84㎡대(±3.5㎡)가 각각 5건 이상인 {u['n']}곳. "
             "단지마다 84㎡대 12개월 중위 − 59㎡대 12개월 중위를 계산한 뒤, 59㎡대 중위 가격대로 묶어 그 중위를 적었습니다.", True)
        + table_a
        + _p("‘중간 50%’는 그 가격대 단지들의 가격차를 줄 세웠을 때 가운데 절반이 놓인 범위입니다. 예시는 두 면적 표본이 많은 단지를 고른 것이며 가격대 대표값이 아닙니다.", True)
        + _h3("[관측] 기준을 바꿔도 비슷한가")
        + table_b
        + _p(f"84㎡대 중위가 59㎡대 중위보다 낮거나 같은 단지: {len(nonpos)}곳 — {nonpos_txt}. 12개월 중위끼리 비교한 값이며 동·층·거래 시점 차이는 보정하지 않았습니다.", True)
        + _p(_split_txt(u["split_hold"]) + _seq_txt(u.get("seq_hold")), True)
        + _edition_section(edition)
        + _h3("[해석]")
        + _p(f"• {_trend_txt(filled)} {_ratio_order_txt(filled, prem_sorted)}<br>"
             "• 같은 단지끼리 비교하면 입지·준공연도 차이는 줄지만, 동·층·향·내부 상태와 거래 시점 차이는 남습니다.<br>"
             "• 이 값은 갈아타기 총비용이 아닙니다. 기존 집을 파는 시점과 새 집을 사는 시점의 차이, 취득세·중개보수·이사비, 대출 조건을 넣지 않았습니다.")
        + _h3("판단이 달라질 조건")
        + _p(f"• 같은 {ss['n']}곳을 최근 6개월만으로 다시 계산한 값이 12개월 값과 크게 달라지면, 거래 시점 차이가 결과를 좌우한다는 뜻이므로 이 해석을 고칩니다.<br>"
             "• 표본 기준을 10건·20건으로 올렸을 때 가격차 중위가 크게 달라지면, 적은 표본 단지가 결과를 끌고 있다는 뜻입니다(위 표).<br>"
             "• 관심 단지가 위 가격대 중위와 다르다면 그 단지 자체의 두 면적 거래를 먼저 봐야 합니다.")
        + _h3("현장에서 확인할 것")
        + _p(("• 두 면적대 최근 거래의 동·향(이 집계는 층을 위 ‘1~2층 제외’ 줄에만 씁니다 — 국토교통부 실거래가 공개시스템에서 확인).<br>" if d3 else
              "• 두 면적대 최근 거래의 층·동(이 집계에는 층·동 정보가 없습니다 — 국토교통부 실거래가 공개시스템에서 확인).<br>")
             + "• 매도와 매수 시점이 어긋날 때의 자금 계획과 세금·대출 조건(적용 시점의 공식 기준).<br>"
             "• 84㎡대 거래가 특정 동·평면에 몰려 있는지.")
        + _clock_section(clock, f"{_ym_txt(u['window'][0])}~{_ym_txt(u['window'][1])}")
        + _h3("후속 관측")
        + _p(_followup_txt(edition, f"{FOLLOW_UP_DATE}에 같은 정의로 다시 계산해, 단지 구성이 바뀐 부분과 가격이 바뀐 부분을 나눠 적습니다. "
                           "이번 값과 크게 다르면 원인(신고 보강·표본 구성·거래 시점)을 먼저 확인한 뒤 해석을 유지·수정·보류 중 하나로 밝힙니다."))
        + _method_section(asof, "59㎡대·84㎡대 = 전용 59·84㎡ ±3.5㎡. 단지 이름 매칭과 K-apt 면적대 세대수 대조를 모두 통과한 단지만 사용(같은 구 동명 단지 의심은 제외). 이상치 제외 없음.", takedown)
    )
    title = f"같은 단지 59㎡대와 84㎡대 실거래 중위는 얼마나 차이 났을까 — {u['n']}곳 ({today})"
    tags = "서울아파트,실거래가,갈아타기,84제곱미터,59제곱미터,국토부실거래,부동산공부"
    return DraftPayload(title=_check(title, "upsize.title"), tags=tags, body=_check(_bind_units(body), "upsize.body"))


def render_deposit_post(res: dict, clock: dict, asof: str, today: str, takedown: str,
                        win: SeriesWindows = DEFAULT_WINDOWS, edition: dict | None = None) -> DraftPayload:
    d = res
    d3 = d.get("spec") == SPEC_D3
    rows = []
    for b in d["bands"]:
        if not b["n"]:
            rows.append([b["band"], "기준 충족 단지 없음", "—", "—", "—"])
            continue
        e = b["example"]
        rows.append([b["band"], f'{b["n"]}곳',
                     f'{_man_txt(b["amount10_median_man"])} (중간 50%: {_man_txt(b["amount10_p25_man"])}~{_man_txt(b["amount10_p75_man"])})',
                     _eok_txt(b["diff_median_eok"]),
                     f'{escape(e["name"])}({escape(e["gu"])}) {e["area_m2"]:g}㎡ 매매 {_eok_txt(e["sale_eok"])}·{e["sale_n"]}건 / '
                     f'전세 {_eok_txt(e["jeonse_eok"])}·{e["jeonse_n"]}건 → 10% {_man_txt(e["amount_man"][0.10])}'])
    table_a = _tbl(["전세가율 구간", "단지 수", "보증금 10% 금액 중위", "매매·전세 중위 차이(중위)", "표본 많은 예시(대표값 아님)"], rows, True)
    srows = [[b["band"], *[_man_txt(b["amount_median_man"][s]) for s in d["shocks"]]] for b in d["bands"] if b["n"]]
    table_b = _tbl(["전세가율 구간", *[f"{round(s * 100)}% 낮아질 때" for s in d["shocks"]]], srows, True)
    hi_band = d["bands"][-1]
    hi_share = hi_band["n"] / d["n"] * 100 if d["n"] else None
    q = "전세를 준 집에서 다음 계약 보증금이 지금보다 낮아지면, 집주인이 돌려줘야 할 차액은 얼마나 될까?"
    a = (f'12개월 매매·전세 실거래가 충분한 서울 아파트 {d["n"]}곳에서, 관측 전세 중위의 10%는 단지 중위 {_man_txt(d["amount10_median_man"])}'
         f'(중간 50%: {_man_txt(d["amount10_p25_man"])}~{_man_txt(d["amount10_p75_man"])})이었습니다.')
    body = (
        _question(q, a)
        + _box("<b>[가정]</b> 지금 보증금이 관측 전세 중위와 같고, 다음 임차인의 보증금이 그보다 5%·10%·20% 낮다고 놓고 계산했습니다. "
               "보증금이 내려간다는 전망이나 그럴 확률이 아닙니다.")
        + _h3("[관측] 전세가율 구간별 보증금 10% 금액")
        + _p("대상: 아파트·전용 40㎡ 이상·12개월 매매 10건 이상·순전세(월세 0) " + ("신규 계약 " if d3 else "")
             + "5건 이상·전세가율 95% 이하, 같은 구 동명 단지 의심 제외. "
             + (f"대표면적 매매 거래 가격이 두 무리로 갈린 {d['n_split_hold']}곳은 다른 단지 거래가 섞였을 수 있어 뺐습니다. " if d.get("n_split_hold") else "")
             + (_seq_txt(d.get("seq_hold"), pair=True).strip() + " " if d3 else "")
             + "금액 = 전세 중위 × 10%. ‘매매·전세 중위 차이’는 두 중위의 차이로, 같은 집의 동시 계약이 아니며 실제로 필요한 돈과 다릅니다.", True)
        + table_a
        + _h3("[관측] 낮아지는 폭을 바꾸면")
        + table_b
        + _box("<b>계산 구조</b> — 매매·전세 중위 차이 대비 돌려줄 돈의 비율 = 낮아지는 폭 × 전세가율 ÷ (1 − 전세가율). "
               "전세가율 50%면 10% 하락은 차이의 10%, 75%면 30%입니다. 실거래 자료 없이도 정해지는 관계이므로, 이 글의 새 정보는 비율이 아니라 "
               "서울 단지들이 어느 전세가율에 얼마나 있는지와 금액의 크기입니다. 매매·전세 중위 차이는 실제 거래에 들어간 돈이 아닙니다.")
        + _edition_section(edition)
        + _h3("[해석]")
        + _p("• 금액은 정의상 전세 중위의 10%라, 전세 가격이 높은 단지일수록 큽니다. 전세가율 구간마다 금액이 다른 것은 구간별 전세 가격 수준이 다르기 때문입니다.<br>"
             f"• 전세가율이 높을수록 같은 하락률의 금액이 매매·전세 중위 차이에서 차지하는 비중이 커집니다. 70% 이상 구간은 {hi_band['n']}곳"
             f"(대상의 {_pct_txt(hi_share, False)})이었고, 그 구간 단지별 비율(돌려줄 금액 ÷ 매매·전세 중위 차이)의 중위는 "
             f"{_pct_txt(hi_band['ratio10_median_pct'], False)}입니다(표의 중위끼리 나눈 값과는 다릅니다).")
        + _h3("이 계산이 놓치는 것")
        + _p((f"• 전세 중위는 신규 계약만으로 냈습니다(매칭된 갱신 계약 {d.get('n_renewal_rows_excluded', 0):,}건 제외). 계약갱신요구권으로 갱신한 계약은 "
              "직전 보증금의 5% 이내로만 올릴 수 있어 신규 전세와 수준이 다를 수 있기 때문입니다. 신규 계약 표본이 5건 미만인 단지는 빠졌습니다.<br>"
              if d3 else
              "• 이 글이 쓴 수집본에는 전세 계약의 신규·갱신 구분을 남기지 않았습니다(국토교통부 원자료에는 있음). 계약갱신요구권으로 갱신한 계약은 "
              "직전 보증금의 5% 이내로만 올릴 수 있어 신규 전세보다 낮을 수 있으므로, 전세 중위가 신규 계약 수준보다 낮게 잡혔을 수 있습니다.<br>")
             + "• 월세·반전세 계약은 제외했습니다. 실제 보증금·만기·대출·보유 현금이 다르면 결과도 달라집니다.<br>"
             "• 취득세·중개보수·대출 이자, 보증보험 조건은 넣지 않았습니다.")
        + _h3("판단이 달라질 조건")
        + _p("• 내 계약의 보증금이 중위와 다르면 금액은 ‘내 보증금 × 낮아지는 폭’으로 다시 계산합니다.<br>"
             "• 만기 무렵 같은 단지의 신규 전세 거래가 가정한 수준(지금 보증금 × (1 − 낮아지는 폭))보다 높으면 여기서 계산한 돌려줄 차액은 실제 차액보다 크게, 낮으면 작게 잡힌 셈입니다.<br>"
             "• 전세 표본이 적은 단지(5~10건)는 한두 건으로 중위가 움직입니다.")
        + _h3("현장에서 확인할 것")
        + _p("• 계약서상 보증금과 만기일, 만기 전후 같은 단지·같은 면적의 최근 신규 전세 거래(국토교통부 전월세 실거래의 계약구분).<br>"
             "• 보증금 반환에 쓸 자금의 출처와 시점, 대출·보증보험 조건(적용 시점의 공식 기준).")
        + _clock_section(clock, "최근 12개월")
        + _h3("후속 관측")
        + _p(_followup_txt(edition, f"{FOLLOW_UP_DATE}에 같은 정의로 다시 계산하되, 이번 단지 목록에 그대로 있는 단지끼리 비교한 값과 새로 들어오거나 빠진 단지를 "
                           "나눠 적습니다. 단지 구성이 바뀐 몫을 떼어 낸 뒤에야 전세가율·전세 가격의 변화로 읽을 수 있습니다."))
        + _method_section(asof, "매매·전세 중위 = 대표 전용면적 ±3.5㎡ 12개월 실거래 중위(12개월 중위의 60% 미만 거래 제외 — 사이트 공개 값과 같음). 전세는 월세 0인 계약만.", takedown)
    )
    title = f"전세보증금이 10% 낮아지면 돌려줄 차액은 얼마일까 — 서울 아파트 {d['n']}곳 실거래로 계산 ({today})"
    tags = "서울아파트,전세가율,전세보증금,실거래가,국토부실거래,부동산공부,전세"
    return DraftPayload(title=_check(title, "deposit.title"), tags=tags, body=_check(_bind_units(body), "deposit.body"))


def _cls_txt(c: str, repeat_days: int) -> str:
    return {"2+": f"{repeat_days}계약일 이상", "1": f"1~{repeat_days - 1}계약일" if repeat_days > 2 else "1계약일", "0": "없음"}[c]


def _wlabel(window: list[str]) -> str:
    a, b = window
    return f"{int(a[4:])}~{int(b[4:])}월" if a[:4] == b[:4] else f"{_ym_txt(a)}~{_ym_txt(b)}"


def _unverified_note(items: list[dict]) -> str:
    """칸 안 신원 미확인 단지 수 문단 — 없으면 빈 문자열."""
    k = sum(1 for c in items if c["identity"] == "미확인")
    if not items or not k:
        return ""
    return _p(f"이 칸 {len(items)}곳 중 {k}곳은 K-apt 면적대 대조로 단지 신원을 확인하지 못한 곳입니다. "
              "신원 확인 단지만으로 다시 센 결과는 아래 설정표 ‘신원 확인 단지만’ 줄에 있습니다.", True)


def render_watch_post(res: dict, grid: list[dict], clock: dict, asof: str, today: str, takedown: str,
                      win: SeriesWindows = DEFAULT_WINDOWS, edition: dict | None = None) -> DraftPayload:
    w = res
    d3 = w.get("spec") == SPEC_D3
    p = w["params"]
    wa, wb = _wlabel(p["windows"][0]), _wlabel(p["windows"][1])
    budget = f'{p["budget_won"] / 1e8:g}억원'
    rep = p["repeat_days"]
    cr = w["cross"]
    keep, lost, gained = cr["2+|2+"], cr["2+|0"], cr["0|2+"]
    cross_rows = [[f"{wa} {budget} 이하 {_cls_txt(a, rep)}",
                   *[f"{cr[f'{a}|{b}']}곳" for b in WATCH_CLASSES]] for a in WATCH_CLASSES]
    table_cross = _tbl([f"{wa} → {wb}", *[f"{wb} {_cls_txt(b, rep)}" for b in WATCH_CLASSES]], cross_rows, True)

    def _named(cell: str) -> str:
        items = w["cells"][cell]
        if not items:
            return _p("해당 단지 없음", True)
        rows = [[f'{escape(c["name"])}({escape(c["gu"])}){" · 신원 미확인" if c["identity"] == "미확인" else ""}',
                 f'{c["n"]["A"]}건 중 {budget} 이하 {c["n_le"]["A"]}건({c["days_le"]["A"]}계약일) · 중위 {_eok_txt(c["med_eok"]["A"])}',
                 f'{c["n"]["B"]}건 중 {budget} 이하 {c["n_le"]["B"]}건({c["days_le"]["B"]}계약일) · 중위 {_eok_txt(c["med_eok"]["B"])}']
                for c in items]
        return _tbl(["단지(구)", wa, wb], rows, True)

    grid_rows = [[escape(g["setting"]) + (f' (제외 {g["params"]["n_rows_excluded"]:,}건)' if "n_rows_excluded" in g["params"] else ""),
                  f'{g["n_pass_both"]}곳', f'{g["cross"]["2+|2+"]}곳',
                  f'{g["cross"]["2+|0"]}곳', f'{g["cross"]["0|2+"]}곳'] for g in grid]
    table_grid = _tbl(["설정", "두 기간 표본 충족", "두 기간 모두 반복", "이전만 반복·최근 없음", "이전 없음·최근만 반복"], grid_rows, True)
    gu_rows = [[g["gu"], f'{g["pass"]}곳 / 패널 {g["panel"]}곳', f'{g["2+|2+"]}곳', f'{g["2+|0"]}곳', f'{g["0|2+"]}곳']
               for g in w["by_gu"] if g["panel"]]
    table_gu = _tbl(["구", "두 기간 표본 충족", "두 기간 모두 반복", "이전만 반복·최근 없음", "이전 없음·최근만 반복"], gu_rows, True)
    sh = w["share_le_pooled_pct"]
    if lost > gained:
        sym = f"‘이전만 반복·최근 없음’ {lost}곳이 ‘이전 없음·최근만 반복’ {gained}곳보다 많았습니다."
    elif lost < gained:
        sym = f"‘이전 없음·최근만 반복’ {gained}곳이 ‘이전만 반복·최근 없음’ {lost}곳보다 많았습니다."
    else:
        sym = f"‘이전만 반복·최근 없음’과 ‘이전 없음·최근만 반복’이 각각 {lost}곳으로 같았습니다."
    q = f"{wa}에 84㎡대가 {budget} 이하로 거래되던 단지에서, {wb}에도 그 가격대 거래가 이어졌을까?"
    a = (f"두 기간 모두 84㎡대 거래가 {p['min_n']}건 이상(서로 다른 계약일 2일 이상)인 {w['n_pass_both']}곳 중, "
         f"두 기간 모두 {budget} 이하 거래가 서로 다른 날 {rep}번 이상 나온 단지 {keep}곳, {wa}에는 그랬지만 {wb}에는 {budget} 이하 거래가 없던 단지 {lost}곳, "
         f"반대로 {wa}에는 없고 {wb}에 반복된 단지 {gained}곳이었습니다.")
    nt = w["n_trades"]
    vol_txt = (f"실제로 표본 충족 {w['n_pass_both']}곳의 84㎡대 거래는 {wa} {nt['A']:,}건, {wb} {nt['B']:,}건이었습니다 — 거래가 줄면 반복 관측도 줄어 "
               "‘이전만 반복’ 칸이 커질 수 있습니다. " if nt["A"] != nt["B"] else "")
    sh_txt = (f"건수가 아니라 비율로 보면, 같은 {w['n_pass_both']}곳의 84㎡대 거래를 모두 합친 {budget} 이하 비중은 {wa} {_pct_txt(sh['A'], False)}, "
              f"{wb} {_pct_txt(sh['B'], False)}였습니다(거래가 많은 단지의 영향이 큽니다)."
              if sh["A"] is not None else "")
    body = (
        _question(q, a)
        + _box(CAUTION_WATCH)
        + _h3(f"[관측] {wa}과 {wb}의 {budget} 이하 거래 반복")
        + _p(f"대상: 사이트 공개 단지 중 아파트(같은 구 동명 단지 의심 제외) {w['n_panel']:,}곳의 전용 {p['area_m2'][0]:g}~{p['area_m2'][1]:g}㎡ 매매. "
             f"두 기간(각 4개월)에 모두 {p['min_n']}건 이상 거래된 {w['n_pass_both']}곳만 분류했습니다. 같은 날 여러 건은 한 계약일로 셉니다. "
             f"표본 부족: {wb}만 {w['short']['B']}곳, {wa}만 {w['short']['A']}곳, 둘 다 {w['short']['both']}곳, 두 기간 거래 없음 {w['short']['no_trades']}곳"
             + (f", 같은 원본 단지명을 두 단지가 공유해 제외 {w['n_shared_source']}곳" if w["n_shared_source"] else "") + ". "
             + _split_txt(w["split_hold"]) + _seq_txt(w.get("seq_hold"))
             + f" (합계 {w['n_panel']:,}곳 = 분류 {w['n_pass_both']}곳 + 표본 부족·거래 없음 {sum(w['short'].values()):,}곳"
             + (f" + 보류 {w['n_split_hold']}곳" if w["n_split_hold"] else "")
             + (f" + 단지일련번호 복수 {w['n_seq_hold']}곳" if w.get("n_seq_hold") else "")
             + (f" + 원본 공유 {w['n_shared_source']}곳" if w["n_shared_source"] else "") + ")", True)
        + table_cross
        + _p(sym + " 두 칸의 크기 차이는 가격 변화만이 아니라 거래 건수와 계약일 분포의 변화로도 생길 수 있어, 이 차이만으로 원인을 가를 수 없습니다. " + vol_txt + sh_txt, True)
        + _h3(f"[관측] {wa}에는 반복, {wb}에는 {budget} 이하 거래 없음")
        + _named("2+|0")
        + _unverified_note(w["cells"]["2+|0"])
        + _h3(f"[관측] {wa}에는 없음, {wb}에 반복")
        + _named("0|2+")
        + _h3("[관측] 설정을 바꾸면")
        + _p("결과가 기준값 하나에 달려 있는지 보려는 표입니다. 결과를 보기 전에 정한 주설정(맨 윗줄)과 함께, 정해 둔 모든 대안 설정의 결과를 그대로 싣습니다.", True)
        + table_grid
        + _h3("[관측] 구별")
        + table_gu
        + _edition_section(edition)
        + _h3("[해석]")
        + _p(f"• 두 기간을 같은 길이(4개월)로 맞춰, 기간 길이 차이 때문에 최근 거래가 적어 보이는 착시를 줄였습니다.<br>"
             f"• 칸마다 단지 수가 작습니다. 한두 건의 층·동 차이로 칸이 바뀔 수 있어, 이 표는 ‘{budget}으로 볼 수 있던 84㎡대 거래가 이어졌는지’를 "
             "단지 목록으로 확인하는 출발점이지 결론이 아닙니다.<br>"
             "• 가격이 올라 같은 예산의 거래가 줄었는지, 거래 자체가 줄었는지, 아직 신고되지 않았는지는 이 자료만으로 가를 수 없습니다.")
        + _h3("판단이 달라질 조건")
        + _p(f"• {_mlabel(win.last)} 계약 신고가 다 들어오면 최근 흐름이 달라질 수 있습니다(위 ‘{_mlabel(win.last)} 포함 참고’ 줄은 아직 덜 들어온 자료).<br>"
             "• 예산을 9억·11억원으로, 반복 기준을 3계약일로 바꾼 줄에서 칸의 크기가 크게 달라지면, 결과가 기준값 주변 단지 몇 곳에 달려 있다는 뜻입니다.<br>"
             "• 신원 미확인 단지를 빼도 같은 방향인지 ‘신원 확인 단지만’ 줄로 확인합니다.")
        + _h3("현장에서 확인할 것")
        + _p((f"• {budget} 이하 거래의 동·향(이 집계는 층을 위 ‘1~2층 제외’ 줄에만 씁니다 — 국토교통부 실거래가 공개시스템에서 확인).<br>" if d3 else
              f"• {budget} 이하 거래의 층·동(이 집계에는 층 정보가 없습니다 — 국토교통부 실거래가 공개시스템에서 확인).<br>")
             + "• 지금 나와 있는 매물 가격은 이 자료에 없습니다. 관심 단지의 현재 매물과 최근 거래를 따로 확인하세요.<br>"
             "• 대출 한도는 주택 가격 구간별로 다르게 적용될 수 있으니 적용 시점의 공식 기준으로 확인합니다.")
        + _clock_section(clock, f"{_ym_txt(p['windows'][0][0])}~{_ym_txt(p['windows'][1][1])}", p["windows"][1][1])
        + _h3("후속 관측")
        + _p(_followup_txt(edition, f"{FOLLOW_UP_DATE}에 ① 같은 계약월 두 기간({wa}·{wb})을 다시 집계해 늦게 들어온 신고·정정이 칸을 얼마나 바꾸는지 보이고, "
                           "② 한 달씩 민 두 기간을 별도 표로 냅니다. 이번 패널 단지 목록을 고정해 두고, 새로 들어온 단지는 따로 셉니다. "
                           "칸의 방향이 바뀌면 해석을 수정하고, 표본 부족이 늘면 판단을 보류합니다."))
        + _method_section(asof, f"84㎡대 = 전용 {p['area_m2'][0]:g}~{p['area_m2'][1]:g}㎡ 거래만(단지 대표면적과 무관). 가격은 총 거래가격(원)으로 {budget} 이하 여부를 판정. "
                                "이상치 제외 없음(이전 기간 중위 60% 미만 제외 결과는 설정표에). 단지 이름 매칭은 사이트 공개 기준과 같음.", takedown)
    )
    title = f"84㎡ {budget} 이하 거래는 이어졌을까 — {wa}과 {wb} 실거래 {w['n_pass_both']}곳 비교 ({today})"
    tags = "서울아파트,84제곱미터,10억아파트,실거래가,국토부실거래,부동산공부,내집마련"
    return DraftPayload(title=_check(title, "watch.title"), tags=tags, body=_check(_bind_units(body), "watch.body"))


def write_series_drafts(results: dict, today: str, outdir: str) -> list[str]:
    """연재 3편 원고(tistory_contract 헬퍼) — {outdir}/drafts/{today}-{kind}-tistory-draft.html."""
    asof, clock = results.get("asof") or today, results["data_clock"]
    td = results.get("takedown") or be._takedown()
    payloads = [("upsize", render_upsize_post(results["upsize"], clock, asof, today, td)),
                ("deposit", render_deposit_post(results["deposit"], clock, asof, today, td)),
                ("watch", render_watch_post(results["watch"], results["watch_grid"], clock, asof, today, td))]
    return [write_helper(f"{outdir}/drafts/{today}-{kind}-tistory-draft.html", payload, today) for kind, payload in payloads]


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="고유 통찰 연재 3종 집계·원고(발행하지 않음)")
    ap.add_argument("--day", default="2026-10-13", help="스냅샷 날짜(dataset-{day}.json, molit-{day}.json.gz)")
    ap.add_argument("--snapshots", default="report/blog/snapshots")
    ap.add_argument("--out", default="report/2026-10-13-insight-series")
    a = ap.parse_args(argv)
    ds = json.load(open(Path(a.snapshots) / f"dataset-{a.day}.json", encoding="utf-8"))
    molit = json.load(gzip.open(Path(a.snapshots) / "molit" / f"molit-{a.day}.json.gz"))
    results = build_series_results(ds, molit)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"series-results-{a.day}.json").write_text(json.dumps(_jsonable(results), ensure_ascii=False, indent=1), encoding="utf-8")
    panel = sorted(results["watch"]["panel"], key=lambda p: (p["gu"], str(p["complex_no"])))   # 후속 추적용 전체 패널(G3)
    (out / f"budget-panel-{a.day}.json").write_text(json.dumps(panel, ensure_ascii=False, indent=1), encoding="utf-8")
    for p in write_series_drafts(results, a.day, str(out)):
        print(p)


if __name__ == "__main__":
    main()
