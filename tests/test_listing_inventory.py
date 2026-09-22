from __future__ import annotations

from datetime import datetime, timezone

from agent_realestate.collectors.naver_region import RegionComplex
from blog.listing_inventory import (
    METHOD_VERSION,
    SCOPE,
    SOURCE,
    build_inventory_snapshot,
    build_inventory_view,
    compare_exact_1d,
    compare_exact_7d,
    compare_snapshots,
    load_snapshot,
    save_snapshot,
)


def row(complex_no: str, sale: int | None, lease: int | None,
        rent: int | None = 0, short: int | None = 0) -> RegionComplex:
    return RegionComplex(
        complex_no, f"단지{complex_no}", 100, "199001", 100,
        deal_count=sale, lease_count=lease, rent_count=rent, short_term_rent_count=short,
    )


def snapshot(scans, mapping, *, observed_at="2026-09-22", success=None, preferred=None):
    return build_inventory_snapshot(scans, mapping, observed_at=observed_at,
                                    districts_expected=("노원", "도봉"), scan_success=success,
                                    preferred_scan_by_complex_no=preferred)


def test_dedupes_boundary_rows_using_actual_gu_not_scan_gu():
    result = snapshot({"노원": [row("1", 2, 3)], "도봉": [row("1", 2, 3), row("2", 0, 4)]},
                      {"1": "도봉", "2": "도봉"})
    assert result["complete"] is True
    assert result["districts"]["노원"] == {
        "sale_article_count": 0, "lease_article_count": 0, "rent_article_count": 0,
        "short_term_rent_article_count": 0, "total_article_count": 0,
        "physical_complex_count": 0, "complexes_with_sale_articles": 0,
    }
    assert result["districts"]["도봉"] == {"sale_article_count": 2, "lease_article_count": 7,
                                             "rent_article_count": 0, "short_term_rent_article_count": 0,
                                             "total_article_count": 9, "physical_complex_count": 2,
                                             "complexes_with_sale_articles": 1}


def test_conflicting_boundary_counts_use_stable_preferred_scan():
    result = snapshot(
        {"노원": [row("1", 2, 3)], "도봉": [row("1", 3, 3)]},
        {"1": "노원"}, preferred={"1": "도봉"},
    )
    assert result["complete"] is True
    assert result["districts"]["노원"]["sale_article_count"] == 3
    assert result["boundary_conflicts_resolved"] == 1


def test_conflicting_counts_inside_one_scan_are_rejected():
    try:
        snapshot({"노원": [row("1", 2, 3), row("1", 3, 3)], "도봉": []}, {"1": "노원"})
    except ValueError as exc:
        assert "within one scan" in str(exc)
    else:
        raise AssertionError("conflicting duplicate inside one scan must fail")


def test_cross_scan_conflict_without_preferred_observation_is_incomplete():
    result = snapshot(
        {"노원": [row("1", 2, 3)], "도봉": [row("1", 3, 3)]},
        {"1": "도봉"}, preferred={"1": "중구"},
    )
    assert result["complete"] is False
    assert {reason["kind"] for reason in result["incomplete_reasons"]} == {"missing_preferred_scan"}


def test_zero_is_counted_but_missing_count_marks_snapshot_incomplete():
    zero = snapshot({"노원": [row("1", 0, 0)], "도봉": []}, {"1": "노원"})
    assert zero["complete"] is True
    assert zero["districts"]["노원"]["physical_complex_count"] == 1
    assert zero["districts"]["노원"]["complexes_with_sale_articles"] == 0

    missing = snapshot({"노원": [row("1", None, 0)], "도봉": []}, {"1": "노원"})
    assert missing["complete"] is False
    assert {r["kind"] for r in missing["incomplete_reasons"]} == {"missing_article_count"}


def test_invalid_direct_scan_count_is_rejected_even_without_json_parser():
    try:
        snapshot({"노원": [row("1", -1, 0)], "도봉": []}, {"1": "노원"})
    except ValueError as exc:
        assert "deal_count" in str(exc)
    else:
        raise AssertionError("negative direct count must fail")


def test_missing_actual_gu_or_scan_never_falls_back_to_scan_district():
    result = snapshot({"노원": [row("1", 8, 1)]}, {})
    assert result["complete"] is False
    assert result["districts"]["노원"]["sale_article_count"] == 0
    assert {r["kind"] for r in result["incomplete_reasons"]} == {"missing_scan", "missing_actual_gu"}


def test_inventory_is_independent_of_monitored_pool_and_compare_handles_zero_baseline():
    base = snapshot({"노원": [row("1", 0, 2)], "도봉": []}, {"1": "노원"}, observed_at="2026-09-15")
    current = snapshot({"노원": [row("1", 4, 2)], "도봉": []}, {"1": "노원"})
    comparison = compare_snapshots(current, base)
    sale = comparison["districts"]["노원"]["sale_article_count"]
    assert sale == {"current": 4, "baseline": 0, "delta": 4, "pct_change": None}
    assert current["source"] == SOURCE and current["scope"] == SCOPE and current["method_version"] == METHOD_VERSION


def test_compare_requires_exact_complete_matching_contract_and_supports_1d_7d():
    current = snapshot({"노원": [row("1", 3, 1)], "도봉": []}, {"1": "노원"})
    prior_1d = snapshot({"노원": [row("1", 2, 1)], "도봉": []}, {"1": "노원"}, observed_at="2026-09-21")
    prior_7d = snapshot({"노원": [row("1", 1, 1)], "도봉": []}, {"1": "노원"}, observed_at="2026-09-15")
    assert compare_exact_1d(current, [prior_1d])["districts"]["노원"]["sale_article_count"]["delta"] == 1
    assert compare_exact_7d(current, [prior_7d])["districts"]["노원"]["sale_article_count"]["delta"] == 2
    assert compare_exact_1d(current, [prior_7d])["districts"]["노원"]["sale_article_count"]["delta"] is None

    incompatible = dict(prior_1d, method_version=METHOD_VERSION + 1)
    assert compare_snapshots(current, incompatible)["compatible"] is False
    assert compare_snapshots(current, incompatible)["districts"]["노원"]["sale_article_count"]["delta"] is None


def test_atomic_snapshot_round_trip_uses_owner_only_mode(tmp_path):
    payload = snapshot({"노원": [], "도봉": []}, {})
    path = tmp_path / "inventory.json"
    save_snapshot(path, payload)
    assert load_snapshot(path) == payload
    assert path.stat().st_mode & 0o777 == 0o600


def test_inventory_view_requires_fresh_complete_observation_for_deltas():
    current = snapshot({"노원": [row("1", 3, 1)], "도봉": []}, {"1": "노원"},
                       observed_at="2026-09-22T07:00:00+09:00")
    prior = snapshot({"노원": [row("1", 2, 1)], "도봉": []}, {"1": "노원"},
                     observed_at="2026-09-21T07:00:00+09:00")
    fresh = build_inventory_view(
        current, [prior], now=datetime(2026, 9, 22, 8, tzinfo=timezone.utc).astimezone(timezone.utc)
    )
    # 07:00 KST is 22:00 UTC on the previous date; at 08:00 UTC age is 10 hours.
    assert fresh["fresh"] is True
    assert fresh["comparisons"]["1d"]["districts"]["노원"]["sale_article_count"]["delta"] == 1

    stale = build_inventory_view(
        current, [prior], now=datetime(2026, 9, 24, 0, tzinfo=timezone.utc)
    )
    assert stale["fresh"] is False
    assert stale["comparisons"]["1d"]["districts"]["노원"]["sale_article_count"]["delta"] is None
