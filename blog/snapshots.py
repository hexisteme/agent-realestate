"""dataset.json 일별 스냅샷(2026-09-06 P0) + 계층 보존·MOLIT 원본 아카이브(2026-09-07 주간결산/월간결산).

매 빌드 후 save_snapshot 가 그날치를 보관한다. 보존 규칙(_should_keep — 파일명 날짜 기준):
  · 모든 날짜 14일(KEEP_DAILY_DAYS) — 7일 전 스냅샷(사실 리드 '패턴' 재현 판정) 용
  · 일요일 400일(KEEP_SUNDAY_DAYS) — 주간결산 base(7일 전 일요일)·월간결산 base(전월 마지막 일요일)
  · 월 마지막 일요일 영구 — 월간결산 장기 비교
load_snapshot_days_ago / load_snapshot_on 은 목표 날짜에 가장 가까운 스냅샷을 ±tolerance 일 이내에서 찾아 반환한다.
save_molit_archive 는 MOLIT 원본(매일 in-place 갱신되어 이력이 없는 파일)을 같은 보존 규칙으로 gzip 보관한다 —
period_delta.diff_filings(신고 델타)의 base. 이 모듈은 저장·조회 계약만 고정 — 렌더러 배선은 period_delta/fact_lead 쪽.
"""
from __future__ import annotations
import glob
import gzip
import json
import os
import re
import shutil
from datetime import date, timedelta

_SNAP_RE = re.compile(r"^dataset-(\d{4}-\d{2}-\d{2})\.json$")
_MOLIT_RE = re.compile(r"^molit-(\d{4}-\d{2}-\d{2})\.json\.gz$")
KEEP_DAILY_DAYS = 14
KEEP_SUNDAY_DAYS = 400


def is_last_sunday_of_month(d: date) -> bool:
    """월 마지막 일요일 — 월간결산 발행일이자 영구 보존 스냅샷."""
    return d.weekday() == 6 and (d + timedelta(days=7)).month != d.month


def _should_keep(d: date, today: date) -> bool:
    """계층 보존 판정: 14일 이내 전부 · 일요일 400일 · 월 마지막 일요일 영구."""
    age = (today - d).days
    if age <= KEEP_DAILY_DAYS:
        return True
    return d.weekday() == 6 and (age <= KEEP_SUNDAY_DAYS or is_last_sunday_of_month(d))


def _prune(dir: str, pattern: str, rx: re.Pattern, today: date) -> None:
    for f in glob.glob(os.path.join(dir, pattern)):
        m = rx.match(os.path.basename(f))
        if m and not _should_keep(date.fromisoformat(m.group(1)), today):
            os.remove(f)


def _date_of(path: str, rx: re.Pattern) -> date:
    m = rx.match(os.path.basename(path))
    if not m:
        raise ValueError(f"스냅샷 파일명 규약 위반: {path}")
    return date.fromisoformat(m.group(1))


def _nearest(dir: str, pattern: str, rx: re.Pattern, target: date, tolerance: int) -> str | None:
    """target 에 가장 가까운 파일(±tolerance 일 이내), 없으면 None."""
    best_gap, best_path = None, None
    for f in glob.glob(os.path.join(dir, pattern)):
        m = rx.match(os.path.basename(f))
        if not m:
            continue
        gap = abs((date.fromisoformat(m.group(1)) - target).days)
        if best_gap is None or gap < best_gap:
            best_gap, best_path = gap, f
    if best_path is None or best_gap > tolerance:
        return None
    return best_path


# ── dataset.json 스냅샷 ────────────────────────────────────────────────────

def save_snapshot(dataset_path: str, today: str, dir: str = "report/blog/snapshots") -> str:
    """dataset_path(오늘자 dataset.json)를 {dir}/dataset-{today}.json 으로 복사하고 보존 규칙 밖의
    스냅샷을 삭제한다(_should_keep). 반환: 새로 쓴 스냅샷 경로."""
    os.makedirs(dir, exist_ok=True)
    dst = os.path.join(dir, f"dataset-{today}.json")
    shutil.copy(dataset_path, dst)
    _prune(dir, "dataset-*.json", _SNAP_RE, date.fromisoformat(today))
    return dst


def list_snapshot_dates(dir: str = "report/blog/snapshots") -> list[date]:
    """보관 중인 스냅샷 날짜(오름차순)."""
    out = []
    for f in glob.glob(os.path.join(dir, "dataset-*.json")):
        m = _SNAP_RE.match(os.path.basename(f))
        if m:
            out.append(date.fromisoformat(m.group(1)))
    return sorted(out)


def snapshot_path_on(target: date, tolerance: int = 1, dir: str = "report/blog/snapshots") -> str | None:
    """target(±tolerance 일)에 가장 가까운 스냅샷 경로 — 실제 base 날짜는 snapshot_date_of 로 되읽는다."""
    return _nearest(dir, "dataset-*.json", _SNAP_RE, target, tolerance)


def snapshot_date_of(path: str) -> date:
    return _date_of(path, _SNAP_RE)


def load_snapshot_on(target: date, tolerance: int = 1, dir: str = "report/blog/snapshots") -> dict | None:
    """target 에 가장 가까운 스냅샷을 ±tolerance 일 이내에서 찾아 파싱해 반환. 없으면 None."""
    p = snapshot_path_on(target, tolerance, dir)
    if not p:
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def load_snapshot_days_ago(days: int = 7, tolerance: int = 1,
                           dir: str = "report/blog/snapshots") -> dict | None:
    """오늘(date.today()) − days 에 가장 가까운 스냅샷(±tolerance 일). 디렉토리·후보 없으면 None."""
    return load_snapshot_on(date.today() - timedelta(days=days), tolerance, dir)


# ── MOLIT 원본 아카이브(신고 델타 base) ────────────────────────────────────

def save_molit_archive(molit_path: str, today: str, dir: str = "report/blog/snapshots/molit") -> str:
    """MOLIT 원본 JSON 을 {dir}/molit-{today}.json.gz 로 보관(≈6MB→1MB) + 같은 보존 규칙으로 가지치기."""
    os.makedirs(dir, exist_ok=True)
    dst = os.path.join(dir, f"molit-{today}.json.gz")
    with open(molit_path, "rb") as src, gzip.open(dst, "wb") as out:
        shutil.copyfileobj(src, out)
    _prune(dir, "molit-*.json.gz", _MOLIT_RE, date.fromisoformat(today))
    return dst


def molit_archive_path_on(target: date, tolerance: int = 1, dir: str = "report/blog/snapshots/molit") -> str | None:
    return _nearest(dir, "molit-*.json.gz", _MOLIT_RE, target, tolerance)


def load_molit_archive_on(target: date, tolerance: int = 1, dir: str = "report/blog/snapshots/molit") -> dict | None:
    p = molit_archive_path_on(target, tolerance, dir)
    if not p:
        return None
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        return json.load(fh)


# ── 거시 지표 스냅샷(macro-{date}.json, 2026-09-07 MacroContext S1) — dataset 과 같은 계층 보존 ──
_MACRO_RE = re.compile(r"^macro-(\d{4}-\d{2}-\d{2})\.json$")


def save_macro_snapshot(payload: dict, today: str, dir: str = "report/blog/snapshots/macro") -> str:
    """collect_macro 결과를 macro-{today}.json 으로 저장하고 계층 보존 규칙으로 정리."""
    os.makedirs(dir, exist_ok=True)
    dst = os.path.join(dir, f"macro-{today}.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    _prune(dir, "macro-*.json", _MACRO_RE, date.fromisoformat(today))
    return dst


def macro_snapshot_path_latest(dir: str = "report/blog/snapshots/macro", on_or_before: date | None = None) -> str | None:
    cands = [(m.group(1), f) for f in glob.glob(os.path.join(dir, "macro-*.json"))
             if (m := _MACRO_RE.match(os.path.basename(f)))
             and (on_or_before is None or date.fromisoformat(m.group(1)) <= on_or_before)]
    return max(cands)[1] if cands else None


def load_macro_snapshot_latest(dir: str = "report/blog/snapshots/macro", on_or_before: date | None = None) -> dict | None:
    """가장 최근(on_or_before 이하) macro 스냅샷, 없으면 None(카드 생략 경로)."""
    p = macro_snapshot_path_latest(dir, on_or_before)
    if not p:
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)
