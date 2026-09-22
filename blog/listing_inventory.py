"""Deterministic inventory snapshots from Naver legal-dong complex lists.

The marker endpoint can return a complex in neighbouring scan bounding boxes.
This module therefore treats the scan district as collection coverage only and
uses an authoritative complex-number-to-actual-district map for every public
aggregate.  An incomplete scan is retained as evidence but cannot be used as a
comparison baseline.
"""
from __future__ import annotations

import json
import os
import tempfile
import copy
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_realestate.collectors.naver_region import RegionComplex, SEOUL_GU

SOURCE = "NAVER_REGIONS_COMPLEXES"
SCOPE = "SEOUL_25GU_ALL_APT_LEGAL_DONG"
METHOD_VERSION = 3
METRICS = (
    "sale_article_count", "lease_article_count", "rent_article_count",
    "short_term_rent_article_count", "total_article_count",
    "physical_complex_count", "complexes_with_sale_articles",
)


def _nonnegative_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a nonnegative integer, not bool")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        parsed = int(value)
    else:
        raise ValueError(f"{field} must be a nonnegative integer or null: {value!r}")
    if parsed < 0:
        raise ValueError(f"{field} must be nonnegative: {value!r}")
    return parsed


def _row_values(
    row: RegionComplex | Mapping[str, Any],
) -> tuple[str, int | None, int | None, int | None, int | None]:
    if isinstance(row, RegionComplex):
        return (row.complex_no,
                _nonnegative_int(row.deal_count, "deal_count"),
                _nonnegative_int(row.lease_count, "lease_count"),
                _nonnegative_int(row.rent_count, "rent_count"),
                _nonnegative_int(row.short_term_rent_count, "short_term_rent_count"))
    if not isinstance(row, Mapping):
        raise TypeError("scan rows must be RegionComplex instances or mappings")
    complex_no = str(row.get("complex_no", row.get("complexNo", ""))).strip()
    return (complex_no,
            _nonnegative_int(row.get("deal_count", row.get("dealCount")), "deal_count"),
            _nonnegative_int(row.get("lease_count", row.get("leaseCount")), "lease_count"),
            _nonnegative_int(row.get("rent_count", row.get("rentCount")), "rent_count"),
            _nonnegative_int(
                row.get("short_term_rent_count", row.get("shortTermRentCount")),
                "short_term_rent_count",
            ))


def _blank_totals() -> dict[str, int]:
    return {metric: 0 for metric in METRICS}


def build_inventory_snapshot(
    scan_rows_by_district: Mapping[str, Iterable[RegionComplex | Mapping[str, Any]]],
    actual_gu_by_complex_no: Mapping[str, str],
    *,
    observed_at: str,
    districts_expected: Iterable[str] = tuple(SEOUL_GU),
    scan_success: Mapping[str, bool] | None = None,
    preferred_scan_by_complex_no: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build one inventory observation without using the scanned district as truth.

    Missing expected scans, failed scans, a missing actual-gu mapping, or an
    omitted article count make the snapshot incomplete.  Valid rows are still
    retained in the aggregates for diagnostics, but consumers must reject an
    incomplete snapshot as a baseline.  Boundary duplicates are resolved from
    one stable preferred scan because a sequential citywide run can span
    several minutes; conflicts inside that one scan remain a hard failure.
    """
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError("observed_at is required")
    expected = tuple(dict.fromkeys(str(gu) for gu in districts_expected))
    if not expected or any(not gu for gu in expected):
        raise ValueError("districts_expected must contain nonempty district names")
    expected_set = set(expected)

    success = {
        gu: bool(scan_success[gu]) if scan_success is not None and gu in scan_success else gu in scan_rows_by_district
        for gu in expected
    }
    issues: list[dict[str, str]] = []
    for gu in expected:
        if gu not in scan_rows_by_district:
            issues.append({"kind": "missing_scan", "district": gu})
        elif not success[gu]:
            issues.append({"kind": "scan_failed", "district": gu})

    observed: dict[str, dict[str, tuple[int | None, int | None, int | None, int | None]]] = {}
    for scan_gu, rows in scan_rows_by_district.items():
        if scan_gu not in expected_set:
            issues.append({"kind": "unexpected_scan_district", "district": str(scan_gu)})
        for row in rows:
            complex_no, sale_count, lease_count, rent_count, short_term_count = _row_values(row)
            if not complex_no:
                issues.append({"kind": "missing_complex_no", "district": str(scan_gu)})
                continue
            counts = (sale_count, lease_count, rent_count, short_term_count)
            by_scan = observed.setdefault(complex_no, {})
            previous = by_scan.get(str(scan_gu))
            if previous is not None and previous != counts:
                raise ValueError(f"conflicting marker counts within one scan for complex_no {complex_no}")
            by_scan[str(scan_gu)] = counts

    by_district = {gu: _blank_totals() for gu in expected}
    boundary_conflicts_resolved = 0
    for complex_no, observations in observed.items():
        actual_gu = actual_gu_by_complex_no.get(complex_no)
        if not actual_gu:
            issues.append({"kind": "missing_actual_gu", "complex_no": complex_no})
            continue
        if actual_gu not in expected_set:
            issues.append({"kind": "invalid_actual_gu", "complex_no": complex_no, "district": str(actual_gu)})
            continue
        preferred_scan = ((preferred_scan_by_complex_no or {}).get(complex_no) or actual_gu)
        counts = observations.get(preferred_scan)
        unique_counts = set(observations.values())
        if counts is None and len(unique_counts) == 1:
            counts = next(iter(unique_counts))
        if counts is None:
            issues.append({"kind": "missing_preferred_scan", "complex_no": complex_no,
                           "district": str(preferred_scan)})
            continue
        if len(unique_counts) > 1:
            boundary_conflicts_resolved += 1
        sale_count, lease_count, rent_count, short_term_count = counts
        if any(value is None for value in counts):
            issues.append({"kind": "missing_article_count", "complex_no": complex_no})
            continue
        totals = by_district[actual_gu]
        totals["sale_article_count"] += sale_count
        totals["lease_article_count"] += lease_count
        totals["rent_article_count"] += rent_count
        totals["short_term_rent_article_count"] += short_term_count
        totals["total_article_count"] += sale_count + lease_count + rent_count + short_term_count
        totals["physical_complex_count"] += 1
        totals["complexes_with_sale_articles"] += int(sale_count > 0)

    total = _blank_totals()
    for district_totals in by_district.values():
        for metric in METRICS:
            total[metric] += district_totals[metric]
    return {
        "source": SOURCE,
        "scope": SCOPE,
        "method_version": METHOD_VERSION,
        "observed_at": observed_at,
        "scan_success": success,
        "districts_expected": list(expected),
        "complete": not issues,
        "incomplete_reasons": issues,
        "districts": by_district,
        "total": total,
        "boundary_conflicts_resolved": boundary_conflicts_resolved,
    }


def _snapshot_date(snapshot: Mapping[str, Any]) -> date:
    observed_at = snapshot.get("observed_at")
    if not isinstance(observed_at, str):
        raise ValueError("snapshot observed_at must be an ISO date or datetime string")
    try:
        return date.fromisoformat(observed_at[:10])
    except ValueError as exc:
        raise ValueError("snapshot observed_at must start with an ISO date") from exc


def snapshots_compatible(current: Mapping[str, Any], baseline: Mapping[str, Any] | None) -> bool:
    """A comparison is valid only for completed observations of one contract."""
    return bool(
        baseline
        and current.get("complete") is True
        and baseline.get("complete") is True
        and current.get("source") == baseline.get("source")
        and current.get("scope") == baseline.get("scope")
        and current.get("method_version") == baseline.get("method_version")
    )


def _metric_change(current: int | None, baseline: int | None, compatible: bool) -> dict[str, int | float | None]:
    if not compatible or current is None or baseline is None:
        return {"current": current, "baseline": baseline, "delta": None, "pct_change": None}
    delta = current - baseline
    return {"current": current, "baseline": baseline, "delta": delta,
            "pct_change": None if baseline == 0 else round(delta / baseline * 100, 1)}


def compare_snapshots(current: Mapping[str, Any], baseline: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return district and total deltas; absent/incompatible baselines yield null deltas."""
    compatible = snapshots_compatible(current, baseline)
    current_districts = current.get("districts", {}) if isinstance(current.get("districts"), Mapping) else {}
    baseline_districts = baseline.get("districts", {}) if compatible and isinstance(baseline.get("districts"), Mapping) else {}
    districts = {}
    for gu in sorted(set(current_districts) | set(baseline_districts)):
        cur_values = current_districts.get(gu, {})
        base_values = baseline_districts.get(gu, {})
        districts[gu] = {metric: _metric_change(cur_values.get(metric), base_values.get(metric), compatible)
                         for metric in METRICS}
    cur_total = current.get("total", {}) if isinstance(current.get("total"), Mapping) else {}
    base_total = baseline.get("total", {}) if compatible and isinstance(baseline.get("total"), Mapping) else {}
    return {
        "observed_at": current.get("observed_at"),
        "baseline_observed_at": baseline.get("observed_at") if baseline else None,
        "compatible": compatible,
        "districts": districts,
        "total": {metric: _metric_change(cur_total.get(metric), base_total.get(metric), compatible)
                  for metric in METRICS},
    }


def compare_exact_days_ago(current: Mapping[str, Any], snapshots: Iterable[Mapping[str, Any]], days: int) -> dict[str, Any]:
    """Compare only to the exact calendar baseline; never use a nearest-date fallback."""
    if days not in (1, 7):
        raise ValueError("only exact 1-day and 7-day comparisons are supported")
    target = _snapshot_date(current) - timedelta(days=days)
    candidates = [snapshot for snapshot in snapshots if _snapshot_date(snapshot) == target]
    if len(candidates) > 1:
        raise ValueError(f"multiple snapshots found for exact {days}-day baseline")
    result = compare_snapshots(current, candidates[0] if candidates else None)
    result["days"] = days
    result["baseline_expected_date"] = target.isoformat()
    return result


def compare_exact_1d(current: Mapping[str, Any], snapshots: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    return compare_exact_days_ago(current, snapshots, 1)


def compare_exact_7d(current: Mapping[str, Any], snapshots: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    return compare_exact_days_ago(current, snapshots, 7)


def snapshot_age_hours(snapshot: Mapping[str, Any], now: datetime) -> float | None:
    """Return observation age while keeping naive/aware datetime contracts explicit."""
    observed = snapshot.get("observed_at")
    if not isinstance(observed, str):
        return None
    try:
        parsed = datetime.fromisoformat(observed)
    except ValueError:
        return None
    if parsed.tzinfo is None and now.tzinfo is not None:
        parsed = parsed.replace(tzinfo=now.tzinfo)
    elif parsed.tzinfo is not None and now.tzinfo is None:
        now = now.replace(tzinfo=parsed.tzinfo)
    return (now - parsed).total_seconds() / 3600


def build_inventory_view(
    current: Mapping[str, Any],
    history: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    max_age_hours: int = 36,
) -> dict[str, Any]:
    """Add freshness and exact-day comparisons without mutating persisted evidence."""
    result = copy.deepcopy(dict(current))
    age_hours = snapshot_age_hours(current, now)
    fresh = bool(
        current.get("complete") is True
        and age_hours is not None
        and 0 <= age_hours <= max_age_hours
    )
    observations = list(history)
    comparisons = {
        "1d": compare_exact_1d(current, observations),
        "7d": compare_exact_7d(current, observations),
    }
    if not fresh:
        for comparison in comparisons.values():
            comparison["compatible"] = False
            for scope in (comparison.get("districts", {}), {"_total": comparison.get("total", {})}):
                for metrics in scope.values():
                    for metric in metrics.values():
                        metric["delta"] = None
                        metric["pct_change"] = None
    result["fresh"] = fresh
    result["age_hours"] = None if age_hours is None else round(age_hours, 2)
    result["comparisons"] = comparisons
    return result


def save_snapshot(path: str | Path, snapshot: Mapping[str, Any], *, mode: int = 0o600) -> None:
    """Atomically persist JSON with owner-only permissions by default."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        os.chmod(target, mode)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_snapshot(path: str | Path) -> dict[str, Any]:
    """Load a JSON object snapshot and reject non-object payloads."""
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("inventory snapshot must be a JSON object")
    return payload
