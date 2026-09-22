"""Build the published apartment scope from complete legal-dong observations.

The legacy frame was captured from map bounding boxes and discarded every
complex below 200 households before the publishing pipeline saw it.  Listing
inventory method v3 already checkpoints every legal dong with its actual gu,
so the same complete observation can safely widen the candidate frame without
reintroducing boundary bleed.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from agent_realestate.collectors.naver_region import SEOUL_GU, parse_region
from blog.listing_inventory import METHOD_VERSION


SCOPE_VERSION = 1
MIN_HOUSEHOLDS = 100


class PublicScopeError(ValueError):
    """A complete, identity-safe public scope could not be built."""


def _read_json(path: Path, code: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicScopeError(code) from exc


def _write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _previous_by_complex_no(path: str | Path | None) -> dict[str, dict]:
    if not path:
        return {}
    previous_path = Path(path)
    if not previous_path.is_file():
        return {}
    value = _read_json(previous_path, "PREVIOUS_FRAME_INVALID")
    if not isinstance(value, list):
        raise PublicScopeError("PREVIOUS_FRAME_SCHEMA")
    indexed: dict[str, dict] = {}
    for row in value:
        if not isinstance(row, dict):
            raise PublicScopeError("PREVIOUS_FRAME_SCHEMA")
        complex_no = str(row.get("complexNo") or "")
        if complex_no:
            indexed.setdefault(complex_no, row)
    return indexed


def load_public_scope(
    scope_dir: str | Path,
    *,
    observation_date: str | None = None,
) -> dict[str, object] | None:
    """Return a complete published scope, or ``None`` for any invalid bundle.

    Consumers validate the three data files against the manifest instead of
    trusting file existence alone.  This also makes a previous complete scope
    a safe fallback when today's Naver observation is incomplete.
    """
    root = Path(scope_dir)
    paths = {
        "public_frame": root / "public-frame.json",
        "survivors": root / "survivors.json",
        "district_map": root / "district-map.json",
        "manifest": root / "manifest.json",
    }
    try:
        manifest = _read_json(paths["manifest"], "MANIFEST_INVALID")
        frame = _read_json(paths["public_frame"], "PUBLIC_FRAME_INVALID")
        survivors = _read_json(paths["survivors"], "SURVIVORS_INVALID")
        district_map = _read_json(paths["district_map"], "DISTRICT_MAP_INVALID")
    except PublicScopeError:
        return None
    if not isinstance(manifest, dict):
        return None
    if (
        manifest.get("scope_version") != SCOPE_VERSION
        or manifest.get("inventory_method_version") != METHOD_VERSION
        or manifest.get("district_count") != len(SEOUL_GU)
        or manifest.get("min_households") != MIN_HOUSEHOLDS
        or not isinstance(manifest.get("observation_date"), str)
        or (observation_date is not None and manifest["observation_date"] != observation_date)
        or not isinstance(frame, list)
        or not isinstance(survivors, list)
        or not isinstance(district_map, dict)
        or manifest.get("frame_count") != len(frame)
        or len(frame) != len(survivors)
        or len(frame) != len(district_map)
    ):
        return None

    try:
        frame_ids = {str(row["complexNo"]) for row in frame if isinstance(row, dict)}
        survivor_ids = {str(row["complex_no"]) for row in survivors if isinstance(row, dict)}
        district_ids = {str(value) for value in district_map}
    except (KeyError, TypeError):
        return None
    if (
        len(frame_ids) != len(frame)
        or frame_ids != survivor_ids
        or frame_ids != district_ids
    ):
        return None
    return {**paths, **manifest}


def latest_public_scope(
    scope_root: str | Path,
    *,
    on_or_before: str | None = None,
) -> dict[str, object] | None:
    """Find the newest complete scope bundle, optionally bounded by date."""
    root = Path(scope_root)
    if not root.is_dir():
        return None
    for candidate in sorted((path for path in root.iterdir() if path.is_dir()), reverse=True):
        if on_or_before is not None and candidate.name > on_or_before:
            continue
        loaded = load_public_scope(candidate)
        if loaded is not None:
            return loaded
    return None


def build_public_scope(
    checkpoint_dir: str | Path,
    output_dir: str | Path,
    *,
    observation_date: str,
    previous_frame_path: str | Path | None = None,
    expected_districts: Iterable[str] = tuple(SEOUL_GU),
    min_households: int = MIN_HOUSEHOLDS,
) -> dict[str, object]:
    """Create a 100+ household frame while preserving hard identity gates.

    Every expected district must have a method-v3 checkpoint for the requested
    date.  A complex number may belong to only one legal district.  Validation
    finishes before any output file is replaced, and the manifest is written
    last so consumers can distinguish a complete scope from interrupted I/O.
    """
    if min_households < 1:
        raise PublicScopeError("MIN_HOUSEHOLDS_INVALID")
    districts = tuple(dict.fromkeys(str(gu) for gu in expected_districts))
    if not districts or any(gu not in SEOUL_GU for gu in districts):
        raise PublicScopeError("DISTRICTS_INVALID")

    checkpoint_root = Path(checkpoint_dir)
    previous = _previous_by_complex_no(previous_frame_path)
    frame: list[dict] = []
    survivors: list[dict] = []
    district_map: dict[str, dict[str, str]] = {}
    observed_gu_by_complex_no: dict[str, str] = {}
    observed_complexes = 0
    excluded_under_min = 0

    for gu in districts:
        payload = _read_json(checkpoint_root / f"{gu}.json", f"CHECKPOINT_MISSING_{gu}")
        if not isinstance(payload, dict):
            raise PublicScopeError(f"CHECKPOINT_SCHEMA_{gu}")
        if (
            payload.get("method_version") != METHOD_VERSION
            or payload.get("observation_date") != observation_date
            or payload.get("district") != gu
            or not isinstance(payload.get("rows"), list)
        ):
            raise PublicScopeError(f"CHECKPOINT_CONTRACT_{gu}")
        try:
            rows = parse_region(payload["rows"], district=gu)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PublicScopeError(f"CHECKPOINT_VALUES_{gu}") from exc
        if not rows:
            raise PublicScopeError(f"CHECKPOINT_EMPTY_{gu}")

        for row in rows:
            observed_complexes += 1
            if not row.complex_no or not row.name:
                raise PublicScopeError(f"COMPLEX_IDENTITY_{gu}")
            previous_gu = observed_gu_by_complex_no.get(row.complex_no)
            if previous_gu is not None:
                raise PublicScopeError(
                    "COMPLEX_DISTRICT_CONFLICT" if previous_gu != gu else "COMPLEX_DUPLICATE"
                )
            observed_gu_by_complex_no[row.complex_no] = gu
            if row.households < min_households:
                excluded_under_min += 1
                continue

            old = previous.get(row.complex_no, {})
            far = old.get("far") or row.far_pct or 0
            built_ym = row.built_ym or str(old.get("builtYm") or "")
            public_row = {
                "complexNo": row.complex_no,
                "name": row.name,
                "lat": row.lat,
                "lng": row.lng,
                "far": far,
                "builtYm": built_ym,
                "households": row.households,
                "dongs": row.dongs,
                "minArea": old.get("minArea"),
                "maxArea": old.get("maxArea"),
                "type": old.get("type") or "아파트",
                "dealCount": row.deal_count,
                "leaseCount": row.lease_count,
                "rentCount": row.rent_count,
                "shortTermRentCount": row.short_term_rent_count,
                "gu": gu,
            }
            frame.append(public_row)
            survivors.append({
                "complex_name": row.name,
                "complex_no": row.complex_no,
                "district": f"서울 {gu}구",
                "gu": gu,
                "households": row.households,
                "built_year": row.built_year,
                "far_pct": far,
            })
            district_map[row.complex_no] = {"sido": "서울특별시", "gu": gu}

    gu_order = {gu: index for index, gu in enumerate(SEOUL_GU)}
    frame.sort(key=lambda row: (gu_order[row["gu"]], row["name"], row["complexNo"]))
    survivors.sort(key=lambda row: (gu_order[row["gu"]], row["complex_name"], row["complex_no"]))
    output_root = Path(output_dir)
    paths = {
        "public_frame": output_root / "public-frame.json",
        "survivors": output_root / "survivors.json",
        "district_map": output_root / "district-map.json",
        "manifest": output_root / "manifest.json",
    }
    manifest = {
        "scope_version": SCOPE_VERSION,
        "inventory_method_version": METHOD_VERSION,
        "observation_date": observation_date,
        "district_count": len(districts),
        "observed_complexes": observed_complexes,
        "min_households": min_households,
        "excluded_under_min": excluded_under_min,
        "frame_count": len(frame),
        "selection": "legal-dong APT, household threshold; MOLIT identity/sample gate downstream",
    }
    # A retry may target an existing date directory.  Remove the completion
    # marker before replacing data files so an interrupted write cannot look
    # like a complete bundle to the daily consumer.
    try:
        paths["manifest"].unlink()
    except FileNotFoundError:
        pass
    _write_json_atomic(paths["public_frame"], frame)
    _write_json_atomic(paths["survivors"], survivors)
    _write_json_atomic(paths["district_map"], district_map)
    _write_json_atomic(paths["manifest"], manifest)
    return {**paths, **manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", required=True)
    parser.add_argument("--checkpoint-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--previous-frame")
    parser.add_argument("--min-households", type=int, default=MIN_HOUSEHOLDS)
    args = parser.parse_args(argv)
    try:
        result = build_public_scope(
            args.checkpoint_dir,
            args.out_dir,
            observation_date=args.today,
            previous_frame_path=args.previous_frame,
            min_households=args.min_households,
        )
    except PublicScopeError as exc:
        print(f"[public-scope] 생성 중단: {exc}")
        return 2
    print(
        f"[public-scope] {result['observed_complexes']:,}단지 관측 → "
        f"{result['min_households']}세대 이상 {result['frame_count']:,}단지"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
