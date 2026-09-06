"""dataset.json 일별 스냅샷(2026-09-06 P0) 단위테스트 — save_snapshot 의 14일 보존 가지치기와
load_snapshot_days_ago 의 ±tolerance 최근접 검색을 검증한다. 가지치기는 파일명 날짜 기준(_SNAP_RE)이라
mtime 이 아니라 파일명 자체를 오래된 날짜로 만들어야 실제로 삭제 분기를 통과한다.
"""
from __future__ import annotations
import json
from datetime import date, timedelta

from blog.snapshots import load_snapshot_days_ago, save_snapshot


def _write_ds(path, n=1):
    path.write_text(json.dumps({"complexes": [{"n": n}], "count": n}, ensure_ascii=False), encoding="utf-8")


def test_save_snapshot_copies_and_prunes_older_than_14_days(tmp_path):
    snap_dir = tmp_path / "snaps"
    snap_dir.mkdir()
    today = "2026-09-06"
    old_date = (date.fromisoformat(today) - timedelta(days=20)).isoformat()
    old_file = snap_dir / f"dataset-{old_date}.json"
    _write_ds(old_file)
    assert old_file.exists()

    ds_path = tmp_path / "dataset.json"
    _write_ds(ds_path, n=7)
    dst = save_snapshot(str(ds_path), today, dir=str(snap_dir))

    assert dst == str(snap_dir / f"dataset-{today}.json")
    assert (snap_dir / f"dataset-{today}.json").exists()
    assert json.loads(open(dst, encoding="utf-8").read())["count"] == 7
    assert not old_file.exists()   # 20일 지난 스냅샷은 14일 보존 가지치기로 삭제


def test_load_snapshot_days_ago_nearest_within_tolerance(tmp_path):
    snap_dir = tmp_path / "snaps"
    snap_dir.mkdir()
    target = date.today() - timedelta(days=7)
    near_date = (target - timedelta(days=1)).isoformat()     # 허용오차(±1일) 이내
    decoy_date = (target - timedelta(days=5)).isoformat()    # 허용오차 밖 — 더 멀지만 존재하는 미끼
    _write_ds(snap_dir / f"dataset-{near_date}.json", n=42)
    _write_ds(snap_dir / f"dataset-{decoy_date}.json", n=999)

    got = load_snapshot_days_ago(days=7, tolerance=1, dir=str(snap_dir))
    assert got is not None and got["count"] == 42   # 미끼가 아니라 최근접 파일이 선택됨


def test_load_snapshot_days_ago_none_when_absent_or_out_of_tolerance(tmp_path):
    snap_dir = tmp_path / "snaps"
    snap_dir.mkdir()
    assert load_snapshot_days_ago(days=7, tolerance=1, dir=str(snap_dir)) is None   # 디렉토리는 있으나 파일 없음

    far_date = (date.today() - timedelta(days=12)).isoformat()   # target(-7일) 대비 5일차 — 허용오차 밖
    _write_ds(snap_dir / f"dataset-{far_date}.json", n=99)
    assert load_snapshot_days_ago(days=7, tolerance=1, dir=str(snap_dir)) is None

    missing_dir = str(tmp_path / "does_not_exist")
    assert load_snapshot_days_ago(days=7, tolerance=1, dir=missing_dir) is None   # 디렉토리 자체가 없음
