"""평형 표의 신원·표본 경계와 단지 단위 중복 차단 회귀."""
from __future__ import annotations

import pytest

from blog.area_tracks import area_track_exclusion, select_area_tracks


def _row(**changes):
    row = {"gu": "노원", "name": "표본단지", "complex_no": "123",
           "product_type": "아파트", "kapt_verified": True,
           "kapt_area_units": {"le60": 200, "60_85": 100, "85_135": 0, "gt135": 0},
           "spread_flag": "", "n59": 5, "n84": 5, "med59_eok": 8.0, "med84_eok": 10.0}
    row.update(changes)
    return row


@pytest.mark.parametrize("changes,reason", [
    ({"product_type": "주상복합"}, "not_apartment"),
    ({"name": ""}, "missing_identity"),
    ({"spread_flag": None}, "spread_unverified"),
    ({"spread_flag": "ambiguous_name"}, "ambiguous_name"),
    ({"spread_flag": "kapt_area_mismatch"}, "kapt_area_mismatch"),
    ({"kapt_verified": False}, "identity_unverified"),
    ({"kapt_verified": "True"}, "identity_unverified"),
    ({"kapt_area_units": None}, "identity_unverified"),
    ({"kapt_area_units": {"le60": 0}}, "identity_unverified"),
    ({"kapt_area_units": {"le60": -1}}, "identity_unverified"),
    ({"n59": 4}, "insufficient_samples"),
    ({"n84": 4}, "insufficient_samples"),
    ({"n59": "5"}, "insufficient_samples"),
    ({"n84": True}, "insufficient_samples"),
    ({"med59_eok": None}, "missing_median"),
    ({"med84_eok": float("nan")}, "missing_median"),
    ({"med59_eok": float("inf")}, "missing_median"),
    ({"med84_eok": 0}, "missing_median"),
    ({"med84_eok": 7}, "total_inversion"),
])
def test_unverified_or_insufficient_area_observations_are_excluded(changes, reason):
    assert area_track_exclusion(_row(**changes)) == reason
    result = select_area_tracks({"complexes": [_row(**changes)]})
    assert result["rows"] == []
    assert result["excluded_count"] == 1
    assert result["exclusions"] == {reason: 1}


def test_exact_five_samples_pass_without_recomputing_existing_medians():
    row = _row()
    result = select_area_tracks({"complexes": [row]})
    assert result["rows"] == [row]
    assert result["eligible_count"] == 1
    assert result["exclusions"] == {}
    assert result["rows"][0]["med59_eok"] == 8.0


def test_area_tracks_sort_by_identity_and_deduplicate_physical_complex():
    rows = [_row(gu="서초", name="나다운단지", complex_no="2", med59_eok=7),
            _row(gu="강남", name="가나다단지", complex_no="1", med59_eok=9),
            _row(gu="강남", name="가나다단지", complex_no="1", med59_eok=9)]
    result = select_area_tracks({"complexes": rows})
    assert [r["name"] for r in result["rows"]] == ["가나다단지", "나다운단지"]
    assert result["eligible_count"] == 2
    assert result["exclusions"] == {"duplicate_identity": 1}


def test_missing_spread_verdict_is_not_a_pass():
    row = _row()
    row.pop("spread_flag")
    assert area_track_exclusion(row) == "spread_unverified"
