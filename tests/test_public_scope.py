from __future__ import annotations

import json

import pytest

from blog import build_explorer
from blog.public_scope import (
    PublicScopeError,
    build_public_scope,
    latest_public_scope,
    load_public_scope,
)


def _checkpoint(path, gu, rows, day="2026-09-22"):
    path.mkdir(parents=True, exist_ok=True)
    (path / f"{gu}.json").write_text(json.dumps({
        "method_version": 3,
        "observation_date": day,
        "district": gu,
        "rows": rows,
    }, ensure_ascii=False), encoding="utf-8")


def _row(complex_no, name, households, *, built="20010101", area_count=3):
    return {
        "complexNo": str(complex_no), "name": name,
        "far": 0, "builtYm": built, "households": households, "dongs": 2,
        "lat": 37.5, "lng": 127.0,
        "dealCount": area_count, "leaseCount": 1, "rentCount": 0,
        "shortTermRentCount": 0,
    }


def test_scope_uses_complete_legal_dong_rows_and_relaxes_households_to_100(tmp_path):
    raw = tmp_path / "raw"
    _checkpoint(raw, "노원", [_row(1, "확장단지", 100), _row(2, "제외단지", 99)])
    _checkpoint(raw, "도봉", [_row(3, "도봉단지", 120)])
    previous = tmp_path / "previous.json"
    previous.write_text(json.dumps([
        {"complexNo": "1", "far": 187, "type": "아파트", "minArea": 44.2, "maxArea": 84.9},
    ], ensure_ascii=False), encoding="utf-8")

    result = build_public_scope(
        raw, tmp_path / "out", observation_date="2026-09-22",
        previous_frame_path=previous, expected_districts=("노원", "도봉"),
    )

    frame = json.loads(result["public_frame"].read_text(encoding="utf-8"))
    survivors = json.loads(result["survivors"].read_text(encoding="utf-8"))
    district_map = json.loads(result["district_map"].read_text(encoding="utf-8"))
    assert {row["complexNo"] for row in frame} == {"1", "3"}
    expanded = next(row for row in frame if row["complexNo"] == "1")
    assert expanded["far"] == 187 and expanded["minArea"] == 44.2
    assert {row["complex_no"] for row in survivors} == {"1", "3"}
    assert district_map == {
        "1": {"sido": "서울특별시", "gu": "노원"},
        "3": {"sido": "서울특별시", "gu": "도봉"},
    }
    assert result["observed_complexes"] == 3
    assert result["excluded_under_min"] == 1
    assert result["frame_count"] == 2


def test_scope_rejects_missing_district_without_publishing_partial_manifest(tmp_path):
    raw = tmp_path / "raw"
    _checkpoint(raw, "노원", [_row(1, "확장단지", 100)])
    output = tmp_path / "out"
    with pytest.raises(PublicScopeError, match="CHECKPOINT_MISSING_도봉"):
        build_public_scope(
            raw, output, observation_date="2026-09-22",
            expected_districts=("노원", "도봉"),
        )
    assert not (output / "manifest.json").exists()


def test_scope_rejects_one_complex_assigned_to_two_legal_districts(tmp_path):
    raw = tmp_path / "raw"
    _checkpoint(raw, "노원", [_row(1, "확장단지", 100)])
    _checkpoint(raw, "도봉", [_row(1, "확장단지", 100)])
    with pytest.raises(PublicScopeError, match="COMPLEX_DISTRICT_CONFLICT"):
        build_public_scope(
            raw, tmp_path / "out", observation_date="2026-09-22",
            expected_districts=("노원", "도봉"),
        )


def test_scope_rejects_duplicate_identity_even_when_below_threshold(tmp_path):
    raw = tmp_path / "raw"
    _checkpoint(raw, "노원", [_row(1, "소규모단지", 99)])
    _checkpoint(raw, "도봉", [_row(1, "소규모단지", 99)])
    with pytest.raises(PublicScopeError, match="COMPLEX_DISTRICT_CONFLICT"):
        build_public_scope(
            raw, tmp_path / "out", observation_date="2026-09-22",
            expected_districts=("노원", "도봉"),
        )


def test_scope_loader_rejects_torn_bundle_and_finds_latest_complete(tmp_path, monkeypatch):
    from blog import public_scope

    monkeypatch.setattr(public_scope, "SEOUL_GU", {"노원": "1135000000"})
    raw = tmp_path / "raw"
    _checkpoint(raw, "노원", [_row(1, "완전단지", 100)], day="2026-09-21")
    old_dir = tmp_path / "scopes" / "2026-09-21"
    build_public_scope(
        raw, old_dir, observation_date="2026-09-21", expected_districts=("노원",),
    )
    broken_dir = tmp_path / "scopes" / "2026-09-22"
    broken_dir.mkdir()
    (broken_dir / "manifest.json").write_text(
        (old_dir / "manifest.json").read_text(encoding="utf-8").replace("2026-09-21", "2026-09-22"),
        encoding="utf-8",
    )

    assert load_public_scope(broken_dir, observation_date="2026-09-22") is None
    latest = latest_public_scope(tmp_path / "scopes", on_or_before="2026-09-22")
    assert latest is not None
    assert latest["observation_date"] == "2026-09-21"


def test_public_dataset_accepts_100_households_and_non_59_84_representative_area(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    _checkpoint(raw, "노원", [_row(1, "확장단지", 100)])
    scope = build_public_scope(
        raw, tmp_path / "scope", observation_date="2026-09-22",
        expected_districts=("노원",),
    )
    molit = tmp_path / "molit.json"
    molit.write_text(json.dumps({
        "11350": [
            {"apt": "확장단지", "area": 45.0, "price": 500_000_000, "ym": f"20260{month}"}
            for month in range(1, 7)
        ],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(build_explorer, "MIN_UNITS", 100)

    dataset = build_explorer.build_dataset_public(
        str(scope["public_frame"]), str(molit), "2026-09-22", "2026-09-22",
        survivors_path=str(scope["survivors"]),
        district_map_path=str(scope["district_map"]),
    )

    assert dataset["count"] == 1
    assert dataset["complexes"][0]["units"] == 100
    assert dataset["complexes"][0]["area_m2"] == 45.0
