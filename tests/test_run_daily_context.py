from __future__ import annotations

from datetime import datetime, timezone

from blog.listing_inventory import build_inventory_snapshot, save_snapshot
from blog.run_daily import _attach_inventory


def _snapshot(observed_at: str, sale: int) -> dict:
    return build_inventory_snapshot(
        {"노원": [{"complexNo": "1", "dealCount": sale, "leaseCount": 2,
                  "rentCount": 1, "shortTermRentCount": 0}]},
        {"1": "노원"}, observed_at=observed_at, districts_expected=("노원",),
    )


def test_attach_inventory_loads_exact_baseline_and_adds_source(tmp_path):
    prior_path = tmp_path / "listing-inventory-2026-09-21.json"
    current_path = tmp_path / "listing-inventory-2026-09-22.json"
    save_snapshot(prior_path, _snapshot("2026-09-21T07:00:00+09:00", 7))
    save_snapshot(current_path, _snapshot("2026-09-22T07:00:00+09:00", 10))

    ds = {"sources": [], "complexes": []}
    out = _attach_inventory(ds, str(current_path), datetime(2026, 9, 22, 1, tzinfo=timezone.utc))
    inventory = out["listing_inventory"]
    assert inventory["fresh"] is True and inventory["status"] == "current"
    assert inventory["comparisons"]["1d"]["districts"]["노원"]["sale_article_count"]["delta"] == 3
    assert out["sources"][0]["name"] == "네이버부동산 법정동별 단지 표시 매물 집계"


def test_attach_inventory_missing_is_explicit_and_does_not_add_source(tmp_path):
    ds = {"sources": [], "complexes": []}
    out = _attach_inventory(ds, str(tmp_path / "missing.json"), datetime.now(timezone.utc))
    assert out["listing_inventory"]["status"] == "missing"
    assert out["listing_inventory"]["fresh"] is False
    assert out["sources"] == []
