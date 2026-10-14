"""연재 정기 발행 — 월 1회·주마다 1편, 판정 통과 시 무인 발행, SPEC-D3(2026-10-14 사용자 결정).

일정(due) → 오늘 스냅샷으로 SPEC-D3 계산 + 지난 판 정의로 다리 계산 → 지난 판과 비교·판정(유지·수정·보류)
→ 원고(report/blog/tistory/{day}-{kind}-tistory-draft.html)와 판 기록(report/blog/series/{kind}/{day}.json),
또는 보류 기록({day}.hold.json)과 텔레그램 알림. 발행은 cron_daily.sh 가 기존 발행기(--kind --date)로 한다.
설계·판정 규칙의 정본: report/2026-10-14-series-automation/DESIGN.md (FOLLOWUP-2026-11-10 을 옮김)."""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import inspect
import json
import math
import os
import shutil
import sys
from collections import Counter
from dataclasses import asdict, replace
from html import escape
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blog.build_explorer as be  # noqa: E402
from blog import insight_series as ins  # noqa: E402
from blog.tistory_contract import write_helper  # noqa: E402
from blog.tistory_delivery import DeliveryError, read_delivery  # noqa: E402

SERIES_START = dt.date(2026, 11, 10)   # 10-14 발행본 직후의 10-20·10-27 은 쉰다
KIND_BY_TUESDAY = {2: "upsize", 3: "deposit", 4: "watch"}
NTH_BY_KIND = {k: n for n, k in KIND_BY_TUESDAY.items()}
KIND_LABEL = {"upsize": "넓혀가기 가격차", "deposit": "보증금 변동 금액", "watch": "예산 이하 체결"}
KEEP, REVISE, HOLD, WAIT = "유지", "수정", "보류", "대기"
CARRY_DAYS = 3                  # 회차 화요일부터 금요일까지 — 로그인 만료·수집 실패 하루로 월 회차를 잃지 않게

PANEL_DRIFT_MAX = 0.10          # 공통 — 지난 판 단지들의 같은 완결 계약월 행 수 변화
UPSIZE_RECOMPUTE_MIN = 0.80     # 연재 1 — 지난 판 단지 중 다시 계산되는 비율
UPSIZE_DIFF_TOL_EOK = 0.20      # 연재 1 — 고정 단지 가격차 중위 허용 폭
UPSIZE_THRESH_GAP_EOK = 0.30    # 연재 1 — 최소표본 10·20 줄과 5 줄의 중위 차
DEPOSIT_COUNT_MIN = 0.90        # 연재 2 — 같은 정의 대상 단지 수 비율
DEPOSIT_DEF_GAP = 0.05          # 연재 2 — 같은 단지끼리 신규만 vs 갱신 포함 10% 금액 중위 차(정의 전환 판)
DEPOSIT_D3_MIN_SHARE = 0.50     # 연재 2 — 신규 계약만으로 계산되는 단지가 같은 자료 갱신 포함 대상의 50% 미만이면 새 정의 적용 불가
WATCH_TRADE_GROWTH_MAX = 0.10   # 연재 3 — 같은 두 창 최근 기간 거래 수 증가
WATCH_CELL_TOL = 2              # 연재 3 — 두 칸 허용 이동(곳)

LEDGER_DIR = "report/blog/series"
DRAFT_DIR = "report/blog/tistory"
SNAPSHOT_DIR = "report/blog/snapshots"
INPUTS_DIR = "report/blog/series/inputs"   # 판에 쓴 원자료 보존 — 일간 스냅샷은 화요일분을 14일 뒤 지운다


# ── 일정 ──────────────────────────────────────────────────────────────────

def due_kind(day: dt.date) -> str | None:
    """둘째·셋째·넷째 화요일 = upsize·deposit·watch. 첫째·다섯째 화요일, 시작일 전, 다른 요일은 None."""
    if day < SERIES_START or day.weekday() != 1:
        return None
    return KIND_BY_TUESDAY.get((day.day - 1) // 7 + 1)


def _nth_tuesday(year: int, month: int, n: int) -> dt.date:
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(1 - first.weekday()) % 7 + 7 * (n - 1))


def next_due(kind: str, day: dt.date) -> dt.date:
    """day 다음의 같은 연재 회차."""
    y, m = day.year, day.month
    while True:
        c = _nth_tuesday(y, m, NTH_BY_KIND[kind])
        if c > day and c >= SERIES_START:
            return c
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def expected_last_month(day: dt.date) -> str:
    """일간 수집은 당월을 빼고 직전 12개월을 받는다 — 실행일의 전월이 수집본의 마지막 계약월이어야 한다."""
    first = day.replace(day=1) - dt.timedelta(days=1)
    return f"{first.year}{first.month:02d}"


def autopublish_on() -> bool:
    return os.environ.get("RE_SERIES_AUTOPUBLISH", "1") != "0"


def scheduled_edition(day: dt.date) -> tuple[str, dt.date] | None:
    """day 가 속한 회차 — 회차 화요일 T 부터 T+CARRY_DAYS 까지. (kind, T)."""
    for back in range(CARRY_DAYS + 1):
        t = day - dt.timedelta(days=back)
        kind = due_kind(t)
        if kind:
            return kind, t
    return None


def open_kind(outroot: str, day: dt.date) -> tuple[str, dt.date] | None:
    """오늘 할 일이 남은 연재 회차 (kind, T) — 유예 기간 안이고, 보류 기록이 없고, 발행 마커가 T 이전이며,
    자동 발행이 꺼졌으면 원고가 아직 없을 때. cron_daily.sh 의 멱등 가드가 이 값이 비어야 조기 종료한다."""
    from blog.tistory_publish_pw import _read_marker, marker_paths
    sched = scheduled_edition(day)
    if sched is None:
        return None
    kind, t = sched
    d = t.isoformat()
    if hold_path(outroot, kind, d).exists() or _read_marker(marker_paths(outroot, kind)[0]) >= d:
        return None
    if publication_url(outroot, kind, d):
        return None
    if not autopublish_on() and publishable(outroot, kind, d) is None:
        return None
    return kind, t


# ── 판 기록 ───────────────────────────────────────────────────────────────

def edition_path(outroot: str, kind: str, day: str) -> Path:
    return Path(outroot) / LEDGER_DIR / kind / f"{day}.json"


def hold_path(outroot: str, kind: str, day: str) -> Path:
    return Path(outroot) / LEDGER_DIR / kind / f"{day}.hold.json"


def draft_path(outroot: str, kind: str, day: str) -> Path:
    return Path(outroot) / DRAFT_DIR / f"{day}-{kind}-tistory-draft.html"


def wait_path(outroot: str, kind: str, day: str) -> Path:
    return Path(outroot) / LEDGER_DIR / kind / f"{day}.wait.json"


def _ledger_days(base: Path) -> list[str]:
    return sorted(p.name[:10] for p in base.glob("????-??-??.json"))


def load_prev_edition(outroot: str, kind: str, day: dt.date) -> dict | None:
    """day 이전에 실제로 발행된 가장 최근 판 — 기록만 있고 발행되지 않은 판(자동 발행 꺼짐·발행 실패)은 건너뛴다."""
    base = Path(outroot) / LEDGER_DIR / kind
    if not base.is_dir():
        return None
    for d in reversed([x for x in _ledger_days(base) if x < day.isoformat()]):
        led = json.loads((base / f"{d}.json").read_text(encoding="utf-8"))
        url = led.get("url") or publication_url(outroot, kind, d)
        if url:
            return {**led, "url": url}
    return None


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def publishable(outroot: str, kind: str, day: str) -> str | None:
    """발행기 연재 게이트 — None 이면 발행 가능. held(보류 기록) · unjudged(판정 기록 없음·보류 판정)
    · draft_mismatch(판정 뒤 원고가 바뀜)."""
    if hold_path(outroot, kind, day).exists():
        return "held"
    ep, dp = edition_path(outroot, kind, day), draft_path(outroot, kind, day)
    try:
        led = json.loads(ep.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unjudged"
    if led.get("verdict") not in (KEEP, REVISE):
        return "unjudged"
    if not dp.exists() or led.get("draft_sha256") != _sha(dp):
        return "draft_mismatch"
    return None


def publication_url(outroot: str, kind: str, day: str) -> str | None:
    try:
        rec = read_delivery(outroot, day, kind)
    except DeliveryError:
        return None
    if not rec or rec.get("state") != "PUBLISHED":
        return None
    return rec.get("url") or None


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(ins._jsonable(obj), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def _canon(v):
    if isinstance(v, (set, frozenset)):
        return sorted(_canon(x) for x in v)
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, float) and math.isinf(v):
        return "inf"
    return v


_FP_CONSTS = ("UPSIZE_ANCHORS_M2", "UPSIZE_TOL_M2", "UPSIZE_MIN_N", "UPSIZE_BANDS", "DEPOSIT_SHOCKS", "DEPOSIT_BANDS",
              "WATCH_AREA_M2", "WATCH_BUDGET_WON", "WATCH_MIN_N", "WATCH_MIN_DAYS", "WATCH_REPEAT_DAYS", "WATCH_PRIOR_CUT",
              "IDENTITY_BLOCKED", "VERIFIED_FLAGS", "SPLIT_RATIO", "SEQ_COVERAGE_MIN", "CONTRACT_COVERAGE_MIN",
              "LOW_FLOOR_MAX", "CONTRACT_TYPES")
_FP_FUNCS = (be.match_molit_names, be._match_records_public, be._median_of, be.passes_rank_gate, be.passes_jeonse_gate,
             be.detect_area_price_split, be.canonical_complex_name, be._collapse_numbered_block, be.build_name_index,
             ins.select_panel_rows, ins._records, ins._upsize_one, ins.classify_window, ins._keep_row, ins._seqs,
             ins._seq_unverified, ins.check_coverage, ins.compute_area_upsize, ins.compute_deposit_change,
             ins.compute_budget_watch, ins.windows_for, ins.months_between, ins.require_complete_months,
             ins.index_named_trades)


def definition_fingerprint() -> str:
    """D2·D3 가 함께 쓰는 정의(상수·매칭·게이트 함수 소스)의 지문 — 바뀌면 같은 정의끼리의 비교가 성립하지 않는다."""
    payload = {"consts": {k: _canon(getattr(ins, k)) for k in _FP_CONSTS}, "outlier_cut": be.OUTLIER_CUT,
               "src": [inspect.getsource(f) for f in _FP_FUNCS]}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def fixed_list(kind: str, res: dict) -> list[dict]:
    """다음 판이 고정해 비교할 단지 목록 — 연재 1·2 는 계산된 단지, 연재 3 은 패널 전체(상태 포함)."""
    src = res["panel"] if kind == "watch" else res["complexes"]
    keep = ("gu", "name", "complex_no", "status") if kind == "watch" else ("gu", "name", "complex_no")
    return [{k: c.get(k) for k in keep} for c in src]


def panel_rows_by_month(fixed: list[dict], molit: dict, months: list[str]) -> dict[str, int]:
    """고정 목록 단지(이름 매칭, 면적 무관)의 계약월별 매매 원거래 행 수 — 대량 정정·해제 감시."""
    ti = ins.index_named_trades(molit)
    c: Counter = Counter()
    for gu, name in sorted({(f["gu"], f["name"]) for f in fixed}):
        for x in ins._records({"gu": gu, "name": name}, ti, months):
            c[x["ym"]] += 1
    return {m: c.get(m, 0) for m in months}


def data_frame(ds: dict) -> dict:
    """비교 가능성의 바탕 — 스캔 범위와 dataset 스키마. 바뀌면 같은 단지 목록끼리의 비교가 성립하지 않는다."""
    return {"scope": os.environ.get("RE_SCAN_SCOPE", "11gu"), "schema_version": ds.get("schema_version")}


def _complete_months(win: ins.SeriesWindows) -> list[str]:
    return ins.months_between(win.year[0], ins.shift_month(win.last, -1))


# ── 판정 ──────────────────────────────────────────────────────────────────

def _eok(v: float | None) -> str:
    return "—" if v is None else f"{ins.half_up(v, 2)}억"


def _r(x: float) -> float:
    return round(x, 6)


def stale_reason(day: dt.date, clock: dict) -> str | None:
    exp = expected_last_month(day)
    if clock.get("last_month") != exp:
        return f"수집본의 마지막 계약월이 {clock.get('last_month')}로, 실행일 기준 {exp}가 아닙니다(낡은 수집본)."
    return None


def common_hold_reasons(day: dt.date, prev: dict | None, clock: dict, fingerprint: str, cur_rows: dict,
                        frame: dict | None = None) -> list[str]:
    """공통 보류 — 지난 판 없음 · 낡은 수집본 · 정의 지문 변경 · 스캔 범위·스키마 변경 · 지난 판 단지들의 완결 계약월 행 수 ±10% 초과 변화."""
    if prev is None:
        return ["지난 판 기록이 없어 비교 기준이 없습니다."]
    reasons = [r for r in (stale_reason(day, clock),) if r]
    if fingerprint != prev.get("fingerprint"):
        reasons.append("연재 계산 정의(상수·매칭·게이트 함수)가 지난 판과 달라 같은 정의끼리 비교할 수 없습니다. "
                       f"정의 변경을 확인했으면 `python3 -m blog.series_edition rebase --kind <kind> --day {prev.get('day')}` 로 "
                       "지난 판을 보존 입력으로 다시 계산한 뒤 다음 슬롯에 다시 시도합니다(보류 기록 삭제 필요).")
    pf = prev.get("frame")
    if frame is not None and pf is not None and pf != frame:
        names = {"scope": "스캔 범위", "schema_version": "dataset 스키마"}
        diff = ", ".join(f"{names.get(k, k)} {pf.get(k)} → {frame.get(k)}" for k in sorted(set(pf) | set(frame)) if pf.get(k) != frame.get(k))
        reasons.append(f"지난 판과 바탕 자료가 달라({diff}) 같은 단지 목록끼리 비교할 수 없습니다.")
    limit = ins.shift_month(prev["last_month"], -1) if prev.get("last_month") else None
    for m, before in sorted((prev.get("panel_rows_by_month") or {}).items()):
        if (limit and m > limit) or m not in cur_rows or not before:
            continue
        ch = (cur_rows[m] - before) / before
        if abs(ch) > PANEL_DRIFT_MAX:
            reasons.append(f"지난 판 단지들의 {m} 계약 거래 행 수가 {before:,} → {cur_rows[m]:,}({ch:+.1%})로 10% 넘게 바뀌었습니다(대량 정정·해제 의심).")
    return reasons


def upsize_split_count(*, own_split: int, site_held: set, pool: set, prev_site_held: set | None) -> dict:
    """연재 1 가격 군집 보류 수 — 연재 자체 판정 + 사이트 보류(dataset held_price_split) 중 지난 판 단지 +
    지난 판 뒤 새로 사이트 보류된 단지. 사이트 보류 단지는 신원 플래그 계산 전에 빠져 연재 후보였는지 알 수 없으므로
    새로 보류된 곳은 모두 넣어 센다(보수적). 지난 판에 사이트 보류 기록이 없으면 지난 판 밖 사이트 보류 전부를 새로 본다."""
    in_pool = site_held & pool
    new = site_held - pool - (prev_site_held or set())
    return {"own": own_split, "pool": len(in_pool), "new": len(new), "total": own_split + len(in_pool) + len(new)}


def judge_upsize(prev: dict, fixed: dict, split_now: int) -> dict:
    """연재 1 — fixed = 지난 판 단지를 지난 판 정의·새 자료로 다시 계산한 결과."""
    hold = []
    pn = prev.get("n") or 0
    if not pn or fixed["n"] / pn < UPSIZE_RECOMPUTE_MIN:
        hold.append(f"지난 판 {pn}곳 중 같은 기준으로 다시 계산되는 곳이 {fixed['n']}곳으로 80% 미만입니다.")
    sp = len(prev.get("split_hold") or [])
    if split_now >= max(2 * sp, sp + 2):
        hold.append(f"가격 군집 보류가 {sp}곳 → {split_now}곳으로 2배 이상(작은 수는 2곳 이상) 늘었습니다.")
    if hold:
        return {"verdict": HOLD, "reasons": hold}
    rev = []
    a, b = prev.get("diff_median_eok"), fixed.get("diff_median_eok")
    if a is None or b is None:
        rev.append("가격차 중위를 비교할 수 없습니다.")
    elif _r(abs(b - a)) > UPSIZE_DIFF_TOL_EOK:
        rev.append(f"지난 판 단지의 가격차 중위가 {_eok(a)} → {_eok(b)}으로 ±0.20억 범위를 벗어났습니다.")
    bands = {x["band"]: x.get("diff_median_eok") for x in fixed.get("bands", [])}
    lo, hi = bands.get("6억 미만"), bands.get("20억 이상")
    if lo is None or hi is None:
        rev.append("양끝 가격대(6억 미만·20억 이상) 중 빈 구간이 있어 금액 순서를 확인하지 못했습니다.")
    elif not lo < hi:
        rev.append(f"금액 순서 '6억 미만 < 20억 이상'이 유지되지 않았습니다({_eok(lo)} · {_eok(hi)}).")
    th = {t["min_n"]: t.get("diff_median_eok") for t in fixed.get("thresholds", [])}
    for k in (10, 20):
        if th.get(5) is not None and th.get(k) is not None and _r(abs(th[k] - th[5])) > UPSIZE_THRESH_GAP_EOK:
            rev.append(f"최소표본 {k}건 줄의 중위({_eok(th[k])})가 5건 줄({_eok(th[5])})과 0.30억 넘게 벌어졌습니다.")
    if rev:
        return {"verdict": REVISE, "reasons": rev}
    return {"verdict": KEEP, "reasons": [f"지난 판 단지의 가격차 중위가 {_eok(a)} → {_eok(b)}으로 ±0.20억 안이고, "
                                         "양끝 가격대의 금액 순서와 표본 기준별 중위도 유지됐습니다."]}


def judge_deposit(prev: dict, bridge_all: dict, held_back: int, cur: dict | None, prev_spec: str, cur_spec: str) -> dict:
    """연재 2 — bridge_all = 지난 판 정의·새 자료 전체, held_back = 사이트 보류로 dataset 에서 빠진 지난 판 단지 수."""
    pn = prev.get("n") or 0
    cnt = bridge_all["n"] + held_back
    if not pn or cnt / pn < DEPOSIT_COUNT_MIN:
        return {"verdict": HOLD, "reasons": [f"같은 정의로 센 대상 단지가 {pn}곳 → {cnt}곳으로 90% 미만입니다."]}
    if prev_spec != cur_spec and cur is not None:
        bn = bridge_all["n"]
        if bn and cur["n"] / bn < DEPOSIT_D3_MIN_SHARE:
            return {"verdict": HOLD, "reasons": [
                f"신규 계약만으로 계산되는 단지가 {cur['n']}곳으로, 같은 자료에서 갱신 계약까지 넣은 대상 {bn}곳의 50% 미만이라 새 정의를 적용할 수 없습니다."]}
        pair = _paired_a10(bridge_all, cur)
        if pair:
            k, a, b = pair
            if a and abs(b - a) / a > DEPOSIT_DEF_GAP:
                return {"verdict": REVISE, "reasons": [
                    f"같은 단지 {k}곳끼리 비교하면 신규 계약만으로 낸 보증금 10% 금액 중위({b:,}만원)가 갱신 계약까지 넣은 값({a:,}만원)과 "
                    f"{(b - a) / a:+.1%} 다릅니다. 지난 판 금액은 갱신 계약을 함께 넣어 낸 값입니다."]}
    return {"verdict": KEEP, "reasons": [f"같은 정의로 센 대상 단지가 {pn}곳 → {cnt}곳으로 유지됐습니다. "
                                         "보증금 10% 금액은 [가정] 계산이라 맞고 틀림을 가르지 않습니다."]}


def _a10(c: dict) -> float | None:
    am = c.get("amount_man") or {}
    return am.get(0.1, am.get("0.1"))


def _paired_a10(before: dict, after: dict) -> tuple[int, int, int] | None:
    """두 결과에 모두 있는 단지끼리의 보증금 10% 금액 중위(만원) — 표본 이탈 효과를 뺀 정의 효과."""
    key = lambda c: (c["gu"], str(c["complex_no"]))   # noqa: E731
    a = {key(c): _a10(c) for c in before.get("complexes", [])}
    b = {key(c): _a10(c) for c in after.get("complexes", [])}
    both = sorted(k for k in a.keys() & b.keys() if a[k] is not None and b[k] is not None)
    if not both:
        return None
    return (len(both), int(ins.half_up(ins.st.median([a[k] for k in both]))),
            int(ins.half_up(ins.st.median([b[k] for k in both]))))


def judge_watch(prev: dict, again: dict) -> dict:
    """연재 3 — again = 지난 판과 같은 두 창을 지난 판 정의·고정 패널·새 자료로 다시 센 결과."""
    pb, ab = prev["n_trades"]["B"], again["n_trades"]["B"]
    if pb and (ab - pb) / pb > WATCH_TRADE_GROWTH_MAX:
        return {"verdict": HOLD, "reasons": [
            f"지난 판과 같은 두 기간을 다시 세니 최근 기간 거래가 {pb:,}건 → {ab:,}건({(ab - pb) / pb:+.1%})으로 10% 넘게 늘었습니다. "
            "지난 판은 늦은 신고 전 자료에 기댔으므로, 해석을 고치기 전에 한 달 민 두 기간을 먼저 봅니다."]}
    pl, pg = prev["cross"]["2+|0"], prev["cross"]["0|2+"]
    al, ag = again["cross"]["2+|0"], again["cross"]["0|2+"]
    lost = {(c["gu"], str(c["complex_no"])) for c in prev["cells"]["2+|0"]}
    def _gain(p: dict) -> bool:   # 창 통과(n≥5)와 별개로 B창 예산 이하 계약일이 생겼는지
        if p.get("b_le_days") is not None:
            return p["b_le_days"] >= 1
        st_ = p.get("status") or ""
        return "|" in st_ and st_.split("|")[1] in ("1", "2+")
    panel = {(p["gu"], str(p["complex_no"])): p for p in again.get("panel", [])}
    gained = [k for k in lost if k in panel and _gain(panel[k])]
    rev = []
    if ag > al:
        rev.append(f"‘이전 없음·최근만 반복’({ag}곳)이 ‘이전만 반복·최근 없음’({al}곳)보다 많아져 방향이 뒤집혔습니다.")
    if lost and len(gained) * 2 >= len(lost):
        rev.append(f"지난 판 ‘이전만 반복·최근 없음’ {len(lost)}곳 중 {len(gained)}곳이 늦은 신고로 최근 기간에 예산 이하 거래를 얻었습니다.")
    if abs(al - pl) > WATCH_CELL_TOL or abs(ag - pg) > WATCH_CELL_TOL:
        rev.append(f"두 칸이 ±{WATCH_CELL_TOL}곳 넘게 움직였습니다(이전만 반복 {pl} → {al}곳, 최근만 반복 {pg} → {ag}곳).")
    if rev:
        return {"verdict": REVISE, "reasons": rev}
    return {"verdict": KEEP, "reasons": [f"지난 판과 같은 두 기간을 늦은 신고까지 넣어 다시 세어도 방향과 크기(±{WATCH_CELL_TOL}곳)가 유지됐습니다"
                                         f"(이전만 반복 {pl} → {al}곳, 최근만 반복 {pg} → {ag}곳)."]}


# ── 지난 판과 비교 표 ─────────────────────────────────────────────────────

def _spec_note(kind: str, cur: dict, bridge_cols: str) -> str:
    seq = cur.get("seq_hold") or []
    base = f"이번 판부터 정의를 {ins.SPEC_D3}로 바꿨습니다 — 국토교통부 단지일련번호 값이 둘 이상 섞였거나 비어 있는 단지를 뺐고(이번 {len(seq)}곳)"
    tail = {"upsize": ", 직거래·1~2층 제외 줄을 더했습니다.",
            "deposit": ", 전세 중위를 신규 계약만으로 냈습니다.",
            "watch": ", 직거래·1~2층 제외 줄을 설정표에 더했습니다."}[kind]
    if kind == "watch":
        return base + tail + f" {bridge_cols} 열은 지난 판 정의(SPEC-D2)로 다시 센 값이라 지난 판과의 차이는 시간 변화만 담고, 새 정의의 결과는 위 주 표에 있습니다."
    return (base + tail + f" {bridge_cols} 열은 지난 판 정의(SPEC-D2)로 새 자료를 다시 계산한 값이라, "
            "지난 판과의 차이는 시간 변화로, ‘이번 판’ 열과의 차이는 정의 변화로 읽습니다.")


def _upsize_view(prev_res, fixed, bridge_all, cur, changed, held_back) -> tuple[list, list, list]:
    cols = [prev_res, fixed] + ([bridge_all] if changed else []) + [cur]
    headers = ["", "지난 판", "지난 판 단지·새 자료"] + (["지난 정의·전체"] if changed else []) + ["이번 판"]
    rows = [["단지 수", *[f'{c["n"]}곳' for c in cols]],
            ["가격차 중위", *[_eok(c.get("diff_median_eok")) for c in cols]],
            ["59㎡대 대비 중위", *[ins._pct_txt(c.get("premium_median_pct")) for c in cols]]]
    key = lambda c: (c["gu"], str(c["complex_no"]))   # noqa: E731
    pk, ck = {key(c) for c in prev_res.get("complexes", [])}, {key(c) for c in cur.get("complexes", [])}
    notes = [f"지난 판 {len(pk)}곳 중 {fixed['n']}곳이 같은 기준으로 다시 계산됐고(빠진 곳 {len(pk) - fixed['n']}곳), "
             f"이번 판에 새로 들어온 곳은 {len(ck - pk)}곳입니다."]
    if changed:
        notes.insert(0, _spec_note("upsize", cur, "‘지난 판 단지·새 자료’·‘지난 정의·전체’"))
        t, d = fixed.get("diff_median_eok"), cur.get("diff_median_eok")
        p, b = prev_res.get("diff_median_eok"), bridge_all.get("diff_median_eok")
        if None not in (t, d, p, b) and abs(d - b) > abs(t - p):
            notes.append(f"정의를 바꾼 효과(가격차 중위 {ins.half_up(d - b, 2):+}억)가 지난 판 이후 시간 변화({ins.half_up(t - p, 2):+}억)보다 큽니다.")
    if held_back:
        notes.append(f"사이트 단지 목록에서 가격 무리가 갈려 보류된 지난 판 단지 {held_back}곳은 ‘지난 판 단지·새 자료’에서 빠졌습니다.")
    return headers, rows, notes


def _deposit_view(prev_res, bridge_all, cur, changed, held_back) -> tuple[list, list, list]:
    cols = [prev_res] + ([bridge_all] if changed else []) + [cur]
    headers = ["", "지난 판"] + (["갱신 포함·새 자료"] if changed else []) + ["이번 판" + (" (신규 계약만)" if changed else "")]
    hi = lambda c: next((b["n"] for b in c.get("bands", []) if b["band"] == "70% 이상"), None)   # noqa: E731
    rows = [["대상 단지 수", *[f'{c["n"]}곳' for c in cols]],
            ["보증금 10% 금액 중위", *[ins._man_txt(c.get("amount10_median_man")) for c in cols]],
            ["전세가율 70% 이상 단지", *[("—" if hi(c) is None else f"{hi(c)}곳") for c in cols]]]
    key = lambda c: (c["gu"], str(c["complex_no"]))   # noqa: E731
    before = {key(c): c["band"] for c in prev_res.get("complexes", [])}
    after = {key(c): c["band"] for c in bridge_all.get("complexes", [])}
    both = before.keys() & after.keys()
    moved = sum(1 for k in both if before[k] != after[k])
    notes = [f"지난 판 {len(before)}곳 중 같은 정의로 이번에도 대상인 {len(both)}곳에서 전세가율 구간이 바뀐 곳 {moved}곳, "
             f"빠진 곳 {len(before.keys() - after.keys())}곳, 새로 들어온 곳 {len(after.keys() - before.keys())}곳입니다."]
    if changed:
        notes.insert(0, _spec_note("deposit", cur, "‘갱신 포함·새 자료’"))
        pair = _paired_a10(bridge_all, cur)
        if pair:
            k, a, b = pair
            notes.append(f"두 정의 모두에서 계산된 같은 단지 {k}곳끼리 보면 보증금 10% 금액 중위는 {a:,}만원(갱신 포함) → {b:,}만원(신규 계약만)입니다. "
                         f"전체 중위 차이의 나머지는 신규 계약이 5건 미만이라 빠진 단지 {bridge_all['n'] - k}곳의 구성 차이입니다.")
    if held_back:
        notes.append(f"사이트 단지 목록에서 가격 무리가 갈려 보류된 지난 판 단지 {held_back}곳은 대상 수 비교에서 그대로 센 뒤 계산에서는 뺐습니다.")
    return headers, rows, notes


def _watch_view(prev_res, again, cur, changed) -> tuple[list, list, list]:
    headers = ["", "지난 판", "같은 두 기간·새 자료"]
    cols = [prev_res, again]
    rows = [["두 기간 표본 충족", *[f'{c["n_pass_both"]}곳' for c in cols]],
            ["두 기간 모두 반복", *[f'{c["cross"]["2+|2+"]}곳' for c in cols]],
            ["이전만 반복·최근 없음", *[f'{c["cross"]["2+|0"]}곳' for c in cols]],
            ["이전 없음·최근만 반복", *[f'{c["cross"]["0|2+"]}곳' for c in cols]],
            ["최근 기간 거래 수", *[f'{c["n_trades"]["B"]:,}건' for c in cols]]]
    pa, pb = prev_res["params"]["windows"]
    ca, cb = cur["params"]["windows"]
    main = ("이 글의 주 표도 같은 두 기간입니다." if [list(pa), list(pb)] == [list(ca), list(cb)]
            else f"이 글의 주 표는 기간을 옮긴 두 기간({ins._wlabel(ca)}·{ins._wlabel(cb)})입니다.")
    key = lambda c: (c["gu"], str(c["complex_no"]))   # noqa: E731
    new_in = len({key(c) for c in cur.get("panel", [])} - {key(c) for c in prev_res.get("panel", [])})
    notes = [f"이 비교는 지난 판과 같은 두 기간({ins._wlabel(pa)}·{ins._wlabel(pb)})을 지난 판 패널로 다시 센 것입니다(늦게 들어온 신고·정정 효과). "
             + main + f" 이번 판 패널에 새로 들어온 단지 {new_in}곳은 이 비교에 없습니다."]
    if changed:
        notes.insert(0, _spec_note("watch", cur, "‘같은 두 기간·새 자료’"))
    return headers, rows, notes


def _fill_note(prev: dict, molit: dict) -> str | None:
    """지난 판 마지막 달(미완결)의 늦은 신고 충원 — 보류 기준이 아니라 공개 지표."""
    pm, before = prev.get("last_month"), (prev.get("last_month_rows") or {}).get(prev.get("last_month"))
    if not pm or before is None:
        return None
    now = panel_rows_by_month(prev["fixed"], molit, [pm])[pm]
    return (f"지난 판의 마지막 달({ins._mlabel(pm)}) 계약은 지난 판 단지 기준 {before:,}건 → {now:,}건이 됐습니다"
            "(계약 후 30일 안에 하는 신고가 늦게 들어오거나 해제된 몫).")


# ── 판 만들기 ─────────────────────────────────────────────────────────────

def _evaluate(outroot: str, day: dt.date, kind: str, ds: dict, molit: dict, jeonse: dict | None, spec: str) -> dict:
    """판정 — WAIT(입력이 아직 안 됨: 낡은 수집본·수집 미완료·전세 없음)은 유예 기간 안에서 다시 시도한다."""
    prev = load_prev_edition(outroot, kind, day)
    clock = ins.summarize_data_clock(molit)
    stale = stale_reason(day, clock)
    if stale:
        return {"verdict": WAIT, "reasons": [stale]}
    if prev is None:
        return {"verdict": HOLD, "reasons": common_hold_reasons(day, prev, clock, "", {})}
    win = ins.windows_for(expected_last_month(day))
    prev_fixed = prev["fixed"]
    cur_rows = panel_rows_by_month(prev_fixed, molit, sorted(set(prev.get("panel_rows_by_month") or {}) & set(ins.months_between(*win.year))))
    reasons = common_hold_reasons(day, prev, clock, definition_fingerprint(), cur_rows, frame=data_frame(ds))
    if reasons:
        return {"verdict": HOLD, "reasons": reasons}
    if kind == "deposit" and jeonse is None:
        return {"verdict": WAIT, "reasons": ["전세 원거래 수집본이 없습니다."]}
    cur = ins.build_series_results(ds, molit, win=win, spec=spec, jeonse=jeonse, kinds=(kind,))
    prev_spec, prev_res = prev["spec"], prev["result"]
    changed = prev_spec != spec
    prev_keys = {(f["gu"], str(f["complex_no"])) for f in prev_fixed}
    site_held = {(h["gu"], str(h["complex_no"])) for h in ds.get("held_price_split") or []}
    ti = ins.index_named_trades(molit)
    if kind == "upsize":
        fixed = ins.compute_area_upsize(ds, molit, trades_index=ti, win=win, spec=prev_spec, only_keys=prev_keys)
        bridge_all = ins.compute_area_upsize(ds, molit, trades_index=ti, win=win, spec=prev_spec) if changed else cur["upsize"]
        pool = prev_keys | {(h["gu"], str(h["complex_no"])) for h in prev_res.get("split_hold") or []}
        prev_site = {(g, str(c)) for g, c in prev["site_held"]} if prev.get("site_held") is not None else None
        sc = upsize_split_count(own_split=len(bridge_all["split_hold"]), site_held=site_held, pool=pool, prev_site_held=prev_site)
        judged = judge_upsize(prev_res, fixed, sc["total"])
        headers, rows, notes = _upsize_view(prev_res, fixed, bridge_all, cur["upsize"], changed, len(site_held & prev_keys))
        if sc["pool"] or sc["new"]:
            notes.append(f"가격 군집 보류 {sc['total']}곳 = 이 연재가 직접 가른 {sc['own']}곳 + 사이트에서 보류한 지난 판 단지 {sc['pool']}곳 + "
                         f"지난 판 뒤 사이트에서 새로 보류한 단지 {sc['new']}곳(연재 후보였는지 알 수 없어 모두 넣어 셈).")
    elif kind == "deposit":
        bridge_all = (ins.compute_deposit_change(ds, molit=molit, trades_index=ti, win=win, spec=prev_spec, jeonse=jeonse)
                      if changed else cur["deposit"])
        held_back = len(site_held & prev_keys)
        judged = judge_deposit(prev_res, bridge_all, held_back, cur["deposit"], prev_spec, spec)
        headers, rows, notes = _deposit_view(prev_res, bridge_all, cur["deposit"], changed, held_back)
    else:
        # ① 늦은 신고 효과만 — 지난 판의 두 창·패널·정의를 고정하고, 군집 판정 기간은 새 마지막 달을 뺀 겹치는 달로 둔다.
        prev_win = replace(ins.windows_for(prev["last_month"]), year=(win.year[0], prev["last_month"]))
        prev_windows = tuple(tuple(w) for w in prev_res["params"]["windows"])
        again = ins.compute_budget_watch(ds, molit, windows=prev_windows, trades_index=ti, win=prev_win, spec=prev_spec,
                                         only_keys=prev_keys)
        judged = judge_watch(prev_res, again)
        headers, rows, notes = _watch_view(prev_res, again, cur["watch"], changed)
    fill = _fill_note(prev, molit)
    if fill:
        notes.append(fill)
    if prev.get("rebased"):
        notes.append(f"‘지난 판’ 열은 {prev['rebased']['at']}에 계산 정의를 고친 뒤 지난 판과 같은 입력으로 다시 낸 값이라 발행본 숫자와 다를 수 있습니다.")
    return {**judged, "prev": prev, "cur": cur, "win": win,
            "edition": {"verdict": judged["verdict"], "reasons": judged["reasons"], "prev_day": prev["day"],
                        "prev_url": prev.get("url"),
                        "headers": [f"{h}({prev['day']})" if h == "지난 판" else h for h in headers], "rows": rows,
                        "notes": notes, "next_date": next_due(kind, day).isoformat()}}


def _render(kind: str, cur: dict, win: ins.SeriesWindows, edition: dict, day: str):
    td = cur.get("takedown") or be._takedown()
    asof = cur.get("asof") or day
    if kind == "upsize":
        return ins.render_upsize_post(cur["upsize"], cur["data_clock"], asof, day, td, win=win, edition=edition)
    if kind == "deposit":
        return ins.render_deposit_post(cur["deposit"], cur["data_clock"], asof, day, td, win=win, edition=edition)
    return ins.render_watch_post(cur["watch"], cur["watch_grid"], cur["data_clock"], asof, day, td, win=win, edition=edition)


def _record(kind: str, day: str, results: dict, ds: dict, molit: dict, *, verdict: str, reasons: list,
            prev_day: str | None, url: str | None, next_date: str | None, draft_sha256: str | None = None,
            snapshot_day: str | None = None, site_held: list | None = None, inputs: dict | None = None) -> dict:
    win = ins.windows_for(results["data_clock"]["last_month"])
    fixed = fixed_list(kind, results[kind])
    res = dict(results[kind])
    if kind == "watch":
        res["grid"] = results["watch_grid"]
    return {"kind": kind, "day": day, "spec": results["spec"], "last_month": win.last, "windows": asdict(win),
            "fingerprint": definition_fingerprint(), "frame": data_frame(ds), "verdict": verdict, "reasons": reasons,
            "prev_day": prev_day, "url": url, "next_date": next_date, "asof": results.get("asof"),
            "snapshot_day": snapshot_day, "draft_sha256": draft_sha256, "data_clock": results["data_clock"], "fixed": fixed,
            "panel_rows_by_month": panel_rows_by_month(fixed, molit, _complete_months(win)),
            "last_month_rows": panel_rows_by_month(fixed, molit, [win.last]),
            "site_held": site_held if site_held is not None else sorted([h["gu"], str(h["complex_no"])] for h in ds.get("held_price_split") or []),
            "inputs": {k: v for k, v in inputs.items() if k != "dir"} if inputs else None, "result": res}


def _notify(text: str) -> None:
    try:
        from agent_realestate.notify.telegram import send_message
        send_message(escape(text))
    except Exception as e:   # 알림 실패는 판정·원고를 바꾸지 않는다
        print(f"[series] 텔레그램 실패: {type(e).__name__}")


def _discard_draft(dp: Path) -> None:
    for p in (dp, dp.with_name(dp.name + ".design_review.json"), dp.with_name(dp.stem + ".design_review.json")):
        p.unlink(missing_ok=True)


def reusable(outroot: str, kind: str, day: str) -> bool:
    """이미 판정된 원고를 다시 계산 없이 쓸 수 있는가 — 판정·원고 sha 가 맞고, 그 뒤 정의 지문·스캔 범위가 그대로일 때만."""
    if publishable(outroot, kind, day) is not None:
        return False
    led = json.loads(edition_path(outroot, kind, day).read_text(encoding="utf-8"))
    return (led.get("fingerprint") == definition_fingerprint()
            and (led.get("frame") or {}).get("scope") == os.environ.get("RE_SCAN_SCOPE", "11gu"))


def record_wait(outroot: str, kind: str, day: dt.date, today: dt.date, reasons: list[str], notify: bool, send=None) -> dict:
    """대기(입력 미비) 기록 — 유예 기간 안에서 다음 슬롯이 다시 본다. 알림은 하루 1번."""
    send = send or _notify
    d = day.isoformat()
    wp = wait_path(outroot, kind, d)
    rec = json.loads(wp.read_text(encoding="utf-8")) if wp.exists() else {"kind": kind, "day": d, "notified": []}
    first = today.isoformat() not in rec["notified"]
    rec["reasons"] = reasons
    if first:
        rec["notified"].append(today.isoformat())
    _write_json(wp, rec)
    if notify and first:
        last = (day + dt.timedelta(days=CARRY_DAYS)).isoformat()
        send(f"⏳ 연재 '{KIND_LABEL[kind]}' {d} 판 입력 대기 — {last}까지 재시도 슬롯마다 다시 봅니다.\n"
             + "\n".join(f"• {r}" for r in reasons))
    return {"status": "wait", "new": first, "kind": kind, "day": d, "reasons": reasons}


def build_edition(outroot: str, day: dt.date, kind: str, *, ds: dict, molit: dict, jeonse: dict | None,
                  notify: bool = True, spec: str = ins.SPEC_D3, today: dt.date | None = None, notifier=None,
                  preserve=None) -> dict:
    """회차 day(T)의 판 — 멱등. 보류 기록이 있으면 보류, 판정된 원고가 그대로면 준비 완료.
    원고를 먼저 쓰고 그 sha256 을 판 기록에 묶는다 — 발행기는 판정 기록과 원고가 맞을 때만 연다(publishable).
    대기(WAIT)는 기록만 남기고 하루 1번 알린다. 보류는 그 회차를 닫는다. preserve 는 판 기록을 쓰기 직전에 입력을
    보존하는 함수(실패하면 판 기록 없이 예외 — 다음 슬롯이 다시 계산·보존)."""
    send = notifier or _notify
    today = today or dt.date.today()
    d = day.isoformat()
    hp, dp, ep = hold_path(outroot, kind, d), draft_path(outroot, kind, d), edition_path(outroot, kind, d)
    if hp.exists():
        _discard_draft(dp)
        return {"status": "held", "new": False, **json.loads(hp.read_text(encoding="utf-8"))}
    if reusable(outroot, kind, d):
        led = json.loads(ep.read_text(encoding="utf-8"))
        return {"status": "ready", "new": False, "verdict": led["verdict"], "reasons": led["reasons"], "draft": str(dp)}
    _discard_draft(dp)   # 판정 안 됐거나 원고가 바뀌었거나 그 뒤 정의·범위가 바뀐 원고 — 다시 계산한다
    ep.unlink(missing_ok=True)
    try:
        ev = _evaluate(outroot, day, kind, ds, molit, jeonse, spec)
    except ins.SpecNotApplicable as e:   # 정의를 적용할 수 없는 자료 — 틀린 글 대신 보류
        ev = {"verdict": HOLD, "reasons": [str(e)]}
    except ValueError as e:              # 수집 미완료 — 유예 기간 안에서 다시
        ev = {"verdict": WAIT, "reasons": [str(e)]}
    if ev["verdict"] == WAIT:
        return record_wait(outroot, kind, day, today, ev["reasons"], notify, send)
    if ev["verdict"] == HOLD:
        rec = {"kind": kind, "day": d, "reasons": ev["reasons"]}
        _write_json(hp, rec)
        if notify:
            send(f"⏸ 연재 '{KIND_LABEL[kind]}' {d} 판 보류 — 발행하지 않았습니다.\n" + "\n".join(f"• {r}" for r in ev["reasons"])
                 + f"\n기록: {hp}")
        return {"status": "held", "new": True, **rec}
    edition = ev["edition"]
    payload = _render(kind, ev["cur"], ev["win"], edition, d)
    write_helper(dp, payload, d)
    inputs = preserve() if preserve else None
    _write_json(ep, _record(kind, d, ev["cur"], ds, molit, verdict=ev["verdict"], reasons=ev["reasons"],
                            prev_day=edition["prev_day"], url=None, next_date=edition["next_date"],
                            draft_sha256=_sha(dp), snapshot_day=today.isoformat(), inputs=inputs))
    if notify and ev["verdict"] == REVISE:
        send(f"✏️ 연재 '{KIND_LABEL[kind]}' {d} 판: 지난 판({edition['prev_url'] or edition['prev_day']}) 해석 '수정' — "
             "새 판은 수정 내용을 밝혀 발행합니다. 지난 발행본 처리(그대로 둠·안내 추가)는 사람이 정합니다.\n"
             + "\n".join(f"• {r}" for r in ev["reasons"]))
    return {"status": "ready", "new": True, "verdict": ev["verdict"], "reasons": ev["reasons"], "draft": str(dp)}


def seed_edition(outroot: str, kind: str, day: str, results: dict, ds: dict, molit: dict, url: str | None,
                 site_held: list | None = None, inputs: dict | None = None) -> Path:
    """첫 판(10-13, SPEC-D2) 기록 — 이후 판이 비교할 기준. site_held 는 그 판 자료의 사이트 가격 군집 보류 [구, 단지번호]."""
    ep = edition_path(outroot, kind, day)
    _write_json(ep, _record(kind, day, results, ds, molit, verdict="첫 판", reasons=[], prev_day=None, url=url,
                            next_date=None, snapshot_day=day, site_held=site_held, inputs=inputs))
    return ep


def _read_gz_json(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def rebase_edition(outroot: str, kind: str, day: str, today: dt.date | None = None) -> dict:
    """정의 지문이 바뀐 뒤(사람이 변경을 확인한 경우) 지난 판을 보존 입력·지금 코드로 다시 계산해 기준을 옮긴다.
    발행 URL·판정은 그대로, 이전 기록은 {day}.json.bak-{옛 지문} 으로 남기고, 다음 판 글에 다시 낸 값임을 밝힌다."""
    ep = edition_path(outroot, kind, day)
    led = json.loads(ep.read_text(encoding="utf-8"))
    inp = Path(outroot) / INPUTS_DIR / f"{day}-{kind}"
    sday = led.get("snapshot_day") or day
    names = led.get("inputs") or {"dataset": f"dataset-{sday}.json.gz", "molit": f"molit-{sday}.json.gz",
                                  "jeonse": f"jeonse-{sday}.json.gz" if kind == "deposit" else None}
    dsp, mp = inp / names["dataset"], inp / names["molit"]
    jp = inp / names["jeonse"] if names.get("jeonse") else None
    if not dsp.exists() or not mp.exists() or (jp is not None and not jp.exists()):
        raise FileNotFoundError(f"보존 입력 없음: {inp} {names}")
    ds, molit = _read_gz_json(dsp), _read_gz_json(mp)
    jeonse = _read_gz_json(jp) if jp else None
    results = ins.build_series_results(ds, molit, win=ins.windows_for(led["last_month"]), spec=led["spec"], jeonse=jeonse,
                                       kinds=(kind,))
    new = _record(kind, day, results, ds, molit, verdict=led["verdict"], reasons=led["reasons"], prev_day=led.get("prev_day"),
                  url=led.get("url") or publication_url(outroot, kind, day), next_date=led.get("next_date"),
                  draft_sha256=led.get("draft_sha256"), snapshot_day=led.get("snapshot_day"), site_held=led.get("site_held"),
                  inputs=led.get("inputs"))
    new["rebased"] = {"from_fingerprint": led.get("fingerprint"), "at": (today or dt.date.today()).isoformat()}
    if jp is not None and jp.name != f"jeonse-{sday}.json.gz":
        new["rebased"]["jeonse_from"] = jp.name   # 그날 전세 보관본이 없어 다른 날 보관본으로 다시 계산
    shutil.copy2(ep, ep.with_name(f"{ep.name}.bak-{led.get('fingerprint')}"))
    _write_json(ep, new)
    return new


# ── CLI ───────────────────────────────────────────────────────────────────

def _snapshot_paths(outroot: str, day: str) -> dict[str, Path]:
    snap = Path(outroot) / SNAPSHOT_DIR
    return {"dataset": snap / f"dataset-{day}.json", "molit": snap / "molit" / f"molit-{day}.json.gz",
            "jeonse": snap / "jeonse" / f"jeonse-{day}.json.gz"}


def _load_inputs(outroot: str, day: str, kind: str) -> tuple[dict, dict, dict | None] | None:
    sp = _snapshot_paths(outroot, day)
    if not sp["dataset"].exists() or not sp["molit"].exists():
        return None
    ds = json.loads(sp["dataset"].read_text(encoding="utf-8"))
    with gzip.open(sp["molit"], "rt", encoding="utf-8") as f:
        molit = json.load(f)
    jeonse = None
    if kind == "deposit":
        arch = sp["jeonse"]
        if not arch.exists():
            from agent_realestate.scope_inputs import resolve_scope_inputs
            src = resolve_scope_inputs(os.environ.get("RE_SCAN_SCOPE", "11gu"), Path(outroot)).get("jeonse")
            if not src or not Path(src).exists():
                return ds, molit, None
            arch.parent.mkdir(parents=True, exist_ok=True)
            with open(src, "rb") as fi, gzip.open(arch.with_name(arch.name + ".tmp"), "wb") as fo:
                shutil.copyfileobj(fi, fo)
            os.replace(arch.with_name(arch.name + ".tmp"), arch)
        with gzip.open(arch, "rt", encoding="utf-8") as f:
            jeonse = json.load(f)
    return ds, molit, jeonse


def preserve_inputs(outroot: str, kind: str, day: str, snapshot_day: str, jeonse_day: str | None = None) -> dict:
    """판에 쓴 스냅샷을 보존한다(dataset 은 gzip) — 다음 달에 이 판을 같은 입력으로 다시 계산할 수 있게.
    다른 날 입력은 지운다. jeonse_day 는 그날 전세 보관본이 없을 때 쓴 다른 날(판 기록 inputs 에 남는다)."""
    dst = Path(outroot) / INPUTS_DIR / f"{day}-{kind}"
    dst.mkdir(parents=True, exist_ok=True)
    for old in dst.glob("*.json.gz"):
        old.unlink()
    sp = _snapshot_paths(outroot, snapshot_day)
    names = {"dir": str(dst), "dataset": f"dataset-{snapshot_day}.json.gz", "molit": sp["molit"].name, "jeonse": None}
    with open(sp["dataset"], "rb") as fi, gzip.open(dst / names["dataset"], "wb") as fo:
        shutil.copyfileobj(fi, fo)
    shutil.copy2(sp["molit"], dst / names["molit"])
    if kind == "deposit":
        js = _snapshot_paths(outroot, jeonse_day or snapshot_day)["jeonse"]
        if js.exists():
            shutil.copy2(js, dst / js.name)
            names["jeonse"] = js.name
    return names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="연재 정기 발행 — due / build / seed")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_due = sub.add_parser("due", help="오늘 할 일이 남은 연재 회차 'kind YYYY-MM-DD' 출력(없으면 빈 줄) — open_kind")
    p_due.add_argument("--day", default=dt.date.today().isoformat())
    p_due.add_argument("--outroot", default=".")
    p_build = sub.add_parser("build", help="판정 + 원고 또는 보류. rc 0=발행 대상 원고, 3=보류, 4=입력 대기, "
                                           "5=자동 발행 꺼짐, 6=연재 회차 아님, 1=판 만들기 실패")
    p_build.add_argument("--day", default=dt.date.today().isoformat(), help="회차 날짜 T(원고·기록·발행 날짜)")
    p_build.add_argument("--today", default=dt.date.today().isoformat(), help="쓸 스냅샷 날짜(유예 기간이면 T 뒤)")
    p_build.add_argument("--kind")
    p_build.add_argument("--outroot", default=".")
    p_build.add_argument("--no-notify", action="store_true")
    p_seed = sub.add_parser("seed", help="첫 판(SPEC-D2) 기록")
    p_seed.add_argument("--results", required=True)
    p_seed.add_argument("--day", required=True)
    p_seed.add_argument("--outroot", default=".")
    p_seed.add_argument("--jeonse-day", help="그날 전세 보관본이 없을 때 대신 보존할 전세 보관본 날짜")
    p_seed.add_argument("--site-held", help="그 판 자료의 사이트 가격 군집 보류 [[구, 단지번호], …] JSON 파일")
    p_rebase = sub.add_parser("rebase", help="정의 변경 확인 뒤 지난 판을 보존 입력으로 다시 계산")
    p_rebase.add_argument("--kind", required=True, choices=ins.SERIES_KINDS)
    p_rebase.add_argument("--day", required=True)
    p_rebase.add_argument("--outroot", default=".")
    a = ap.parse_args(argv)
    from agent_realestate import config
    config.load_env_file()
    if a.cmd == "due":
        opened = open_kind(a.outroot, dt.date.fromisoformat(a.day))
        print(f"{opened[0]} {opened[1].isoformat()}" if opened else "")
        return 0
    if a.cmd == "seed":
        results = json.loads(Path(a.results).read_text(encoding="utf-8"))
        loaded = _load_inputs(a.outroot, a.day, "upsize")
        if loaded is None:
            print(f"[series] {a.day} 스냅샷 없음")
            return 4
        ds, molit, _ = loaded
        site_held = json.loads(Path(a.site_held).read_text(encoding="utf-8"))["site_held"] if a.site_held else None
        for kind in ins.SERIES_KINDS:
            if edition_path(a.outroot, kind, a.day).exists():
                print(f"[series] 이미 있음: {edition_path(a.outroot, kind, a.day)}")
                continue
            inputs = preserve_inputs(a.outroot, kind, a.day, a.day, jeonse_day=a.jeonse_day)
            print(seed_edition(a.outroot, kind, a.day, results, ds, molit, publication_url(a.outroot, kind, a.day),
                               site_held=site_held, inputs=inputs), "· 입력 보존:", inputs)
        return 0
    if a.cmd == "rebase":
        new = rebase_edition(a.outroot, a.kind, a.day)
        print(f"[series] {a.kind} {a.day} 다시 계산 — 지문 {new['rebased']['from_fingerprint']} → {new['fingerprint']}")
        return 0
    day, today = dt.date.fromisoformat(a.day), dt.date.fromisoformat(a.today)
    kind = a.kind or due_kind(day)
    if kind not in ins.SERIES_KINDS:
        print(f"[series] {a.day}: 연재 회차 아님")
        return 6
    try:
        loaded = None
        if not hold_path(a.outroot, kind, a.day).exists() and not reusable(a.outroot, kind, a.day):
            loaded = _load_inputs(a.outroot, a.today, kind)
            if loaded is None:
                out = record_wait(a.outroot, kind, day, today, [f"{a.today} 스냅샷이 아직 없습니다(일간 실행 뒤 다시 봅니다)."],
                                  notify=not a.no_notify)
                print(f"[series] {kind} {a.day}: 대기 — {out['reasons'][0]}")
                return 4
        ds, molit, jeonse = loaded or ({}, {}, None)
        out = build_edition(a.outroot, day, kind, ds=ds, molit=molit, jeonse=jeonse, notify=not a.no_notify, today=today,
                            preserve=lambda: preserve_inputs(a.outroot, kind, a.day, a.today))
    except Exception as e:   # 입력 읽기·계산 결함 — 원고 없이 알리고 다음 재시도 슬롯에 맡긴다
        if not a.no_notify:
            _notify(f"❌ 연재 '{KIND_LABEL[kind]}' {a.day} 판 만들기 실패 — {type(e).__name__}: {e}")
        print(f"[series] {kind} {a.day}: 실패 {type(e).__name__}: {e}")
        return 1
    print(f"[series] {kind} {a.day}: {out['status']} {out.get('verdict', '')} — {' / '.join(out.get('reasons', []))}")
    if out["status"] == "wait":
        return 4
    if out["status"] != "ready":
        return 3
    if not autopublish_on():
        if out["new"] and not a.no_notify:
            _notify(f"📝 연재 '{KIND_LABEL[kind]}' {a.day} 원고 준비(판정 {out['verdict']}) — 자동 발행 꺼짐(RE_SERIES_AUTOPUBLISH=0).\n{out['draft']}")
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
