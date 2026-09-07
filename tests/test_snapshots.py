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


# ── 계층 보존·날짜 지정 로더·MOLIT 아카이브 (2026-09-07 주간결산/월간결산) ──

from blog.snapshots import (_should_keep, is_last_sunday_of_month, load_molit_archive_on, load_snapshot_on,
                            save_molit_archive)


def test_should_keep_tiers():
    today = date(2026, 12, 6)
    assert _should_keep(date(2026, 11, 25), today)          # 11일 — 일반 보존
    assert not _should_keep(date(2026, 11, 17), today)      # 19일 화요일 → 삭제
    assert _should_keep(date(2026, 9, 13), today)           # 일요일 84일 → 보존
    assert not _should_keep(date(2025, 9, 14), today)       # 일요일 448일, 월 마지막 아님 → 삭제
    assert _should_keep(date(2025, 9, 28), today)           # 2025-09 마지막 일요일 → 영구
    assert is_last_sunday_of_month(date(2026, 9, 27)) and not is_last_sunday_of_month(date(2026, 9, 20))


def test_save_snapshot_keeps_sunday_beyond_14_days(tmp_path):
    d = tmp_path / "s"; d.mkdir()
    _write_ds(d / "dataset-2026-09-06.json")   # 일요일, 28일 전
    _write_ds(d / "dataset-2026-09-08.json")   # 화요일, 26일 전
    _write_ds(tmp_path / "dataset.json", n=3)
    save_snapshot(str(tmp_path / "dataset.json"), "2026-10-04", dir=str(d))
    assert (d / "dataset-2026-09-06.json").exists() and not (d / "dataset-2026-09-08.json").exists()


def test_load_snapshot_on_and_molit_archive_roundtrip(tmp_path):
    d = tmp_path / "s"; d.mkdir()
    _write_ds(d / "dataset-2026-09-06.json", n=3)
    assert load_snapshot_on(date(2026, 9, 7), 1, str(d))["count"] == 3
    assert load_snapshot_on(date(2026, 9, 9), 1, str(d)) is None
    m = tmp_path / "molit.json"
    m.write_text(json.dumps({"11680": [{"apt": "A"}], "_done": []}), encoding="utf-8")
    p = save_molit_archive(str(m), "2026-09-13", dir=str(tmp_path / "m"))
    assert p.endswith("molit-2026-09-13.json.gz")
    assert load_molit_archive_on(date(2026, 9, 13), 0, str(tmp_path / "m"))["11680"][0]["apt"] == "A"
    assert load_molit_archive_on(date(2026, 9, 6), 0, str(tmp_path / "m")) is None
