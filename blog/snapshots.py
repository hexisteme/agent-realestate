"""dataset.json 일별 스냅샷(2026-09-06 P0) — 주간/기간 비교(다음 단계 배선)를 위한 저장소.
매 빌드 후 save_snapshot 가 그날치를 보관하고 14일 지난 스냅샷은 삭제한다(무한 누적 방지).
load_snapshot_days_ago 는 (오늘 − N일)에 가장 가까운 스냅샷을 ±tolerance 일 이내에서 찾아 반환한다.
이 모듈은 저장·조회 계약만 고정 — 로더 반환값을 실제 렌더러에 배선하는 것은 다음 라운드 작업이다.
"""
from __future__ import annotations
import glob
import json
import os
import re
import shutil
from datetime import date, timedelta

_SNAP_RE = re.compile(r"^dataset-(\d{4}-\d{2}-\d{2})\.json$")


def save_snapshot(dataset_path: str, today: str, dir: str = "report/blog/snapshots") -> str:
    """dataset_path(오늘자 dataset.json)를 {dir}/dataset-{today}.json 으로 복사하고, 14일보다
    오래된 스냅샷 파일을 삭제한다. 반환: 새로 쓴 스냅샷 경로."""
    os.makedirs(dir, exist_ok=True)
    dst = os.path.join(dir, f"dataset-{today}.json")
    shutil.copy(dataset_path, dst)
    cutoff = date.fromisoformat(today) - timedelta(days=14)
    for f in glob.glob(os.path.join(dir, "dataset-*.json")):
        m = _SNAP_RE.match(os.path.basename(f))
        if m and date.fromisoformat(m.group(1)) < cutoff:
            os.remove(f)
    return dst


def load_snapshot_days_ago(days: int = 7, tolerance: int = 1,
                           dir: str = "report/blog/snapshots") -> dict | None:
    """오늘(date.today()) − days 에 가장 가까운 스냅샷을, ±tolerance 일 이내에서 찾아 파싱해 반환.
    디렉토리가 없거나 후보가 없거나 가장 가까운 것도 허용오차 밖이면 None."""
    target = date.today() - timedelta(days=days)
    best_gap, best_path = None, None
    for f in glob.glob(os.path.join(dir, "dataset-*.json")):
        m = _SNAP_RE.match(os.path.basename(f))
        if not m:
            continue
        gap = abs((date.fromisoformat(m.group(1)) - target).days)
        if best_gap is None or gap < best_gap:
            best_gap, best_path = gap, f
    if best_path is None or best_gap > tolerance:
        return None
    with open(best_path, encoding="utf-8") as fh:
        return json.load(fh)
