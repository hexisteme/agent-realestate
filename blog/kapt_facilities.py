"""K-apt 난방·주차 전용 주기 보강 캐시.

공개 데이터셋의 실제 발행 단지를 대상으로 ``complex_no``를 K-apt 코드에 연결한다. 이름만
비슷한 단지를 채택하지 않고 기존의 구·세대수·준공연도·주소 신원 게이트를 그대로 재사용한다.
공식 응답 원문과 서비스키는 저장하지 않으며, 완전한 한 회차가 끝나기 전에는 이전 캐시를
교체하지 않는다.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from agent_realestate.collectors.kapt import (
    KAPT_DATASET_URL,
    clear_last_service_error,
    fetch_basis,
    last_service_error_code,
)
from agent_realestate.identity import verify_kapt_basis_identity
from collect_universe_enrich import _resolve_kapt_basis


CACHE_SCHEMA = 1
SOURCE_NAME = "K-apt 공동주택 기본정보 OpenAPI"
DEFAULT_DATASET = Path("report/blog/dataset.json")
DEFAULT_OUTPUT = Path("report/enrichment/kapt-facilities.json")
FACILITY_FIELDS = (
    "heating",
    "corridor_type",
    "builder",
    "parking_ground",
    "parking_underground",
    "parking_total",
    "parking_household_count",
    "parking_per_unit",
)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


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


def _district(gu: str) -> str:
    clean = gu.strip()
    return f"서울 {clean if clean.endswith('구') else clean + '구'}"


def _identity(row: dict) -> dict:
    return {
        "name": str(row.get("name") or "").strip(),
        "gu": str(row.get("gu") or "").strip(),
        "units": int(row.get("units") or 0),
        "built_year": int(row.get("built_year") or 0),
    }


def _frame_index(path: str | Path | None) -> dict[str, dict]:
    if not path:
        return {}
    rows = _read_json(Path(path), [])
    if not isinstance(rows, list):
        return {}
    indexed: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        complex_no = str(row.get("complexNo") or "").strip()
        if not complex_no:
            continue
        built = str(row.get("builtYm") or "")[:4]
        indexed.setdefault(complex_no, {
            "match_name": str(row.get("name") or "").strip(),
            "units": int(row.get("households") or 0),
            "built_year": int(built) if built.isdigit() else 0,
        })
    return indexed


def _path_observed_date(path: Path) -> str | None:
    matches = re.findall(r"(20\d{6})", path.name)
    if not matches:
        return None
    raw = matches[-1]
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def _seed_records(paths: tuple[str | Path, ...]) -> dict[str, dict]:
    """Load only previously identity-verified ``complex_no → kapt_code`` evidence."""
    candidates: dict[str, dict[str, dict]] = {}
    for raw_path in paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        value = _read_json(path, {})
        if isinstance(value, dict) and isinstance(value.get("complexes"), dict):
            rows = ((key, row) for key, row in value["complexes"].items())
        elif isinstance(value, dict):
            rows = ((key, row) for key, row in value.items())
        elif isinstance(value, list):
            rows = ((str(row.get("complex_no") or ""), row) for row in value if isinstance(row, dict))
        else:
            continue
        for complex_no, row in rows:
            if not isinstance(row, dict) or row.get("kapt_verified") is not True or not row.get("kapt_code"):
                continue
            code = str(row["kapt_code"])
            record = candidates.setdefault(str(complex_no), {}).setdefault(code, {
                "kapt_code": code,
                "observed_date": _path_observed_date(path),
            })
            if _path_observed_date(path):
                record["observed_date"] = max(record.get("observed_date") or "", _path_observed_date(path) or "")
            for field in FACILITY_FIELDS:
                if row.get(field) is not None:
                    record[field] = row[field]
    # 서로 다른 검증코드가 같은 complex_no를 주장하면 어느 것도 자동 채택하지 않는다.
    return {
        complex_no: next(iter(by_code.values()))
        for complex_no, by_code in candidates.items()
        if len(by_code) == 1
    }


def _verified_seed_basis(code: str, identity: dict, key: str, basis_cache: dict[str, dict | None]) -> dict | None:
    if code not in basis_cache:
        basis_cache[code] = fetch_basis(code, key)
    basis = basis_cache[code]
    if not basis:
        return None
    basis_units = int(basis.get("units") or basis.get("hoCnt") or 0)
    units = int(identity.get("units") or 0)
    built_year = int(identity.get("built_year") or 0)
    basis_year = int(basis.get("built_year") or 0)
    u_known = bool(units and basis_units)
    y_known = bool(built_year and basis_year)
    u_ok = not u_known or abs(basis_units - units) <= max(3, int(units * 0.15))
    y_ok = not y_known or abs(basis_year - built_year) <= 2
    strong = (u_known and u_ok) or (y_known and y_ok)
    if not (u_ok and y_ok and strong):
        return None
    if verify_kapt_basis_identity(identity["match_name"], _district(identity["gu"]), units, basis):
        return None
    return basis


def _same_target(entry: dict, identity: dict) -> bool:
    return all(entry.get(key) == value for key, value in identity.items())


def _recent(day_text: str | None, today: date, max_age_days: int) -> bool:
    try:
        observed = date.fromisoformat(str(day_text))
    except ValueError:
        return False
    age = (today - observed).days
    return 0 <= age < max_age_days


def _cache_payload(entries: dict[str, dict], *, today: str, target_count: int, complete: bool) -> dict:
    verified = sum(1 for entry in entries.values() if entry.get("kapt_verified") is True)
    observed_dates = sorted(
        str(entry["observed_date"])
        for entry in entries.values()
        if entry.get("kapt_verified") is True and entry.get("observed_date")
    )
    return {
        "_meta": {
            "schema": CACHE_SCHEMA,
            "source_name": SOURCE_NAME,
            "source_url": KAPT_DATASET_URL,
            "generated_at": f"{today}T00:00:00+09:00",
            "latest_observed_date": observed_dates[-1] if observed_dates else None,
            "target_count": target_count,
            "verified_count": verified,
            "complete": complete,
        },
        "complexes": entries,
    }


def _load_entries(path: Path) -> dict[str, dict]:
    value = _read_json(path, {})
    if not isinstance(value, dict):
        return {}
    meta = value.get("_meta")
    entries = value.get("complexes")
    if not isinstance(meta, dict) or meta.get("schema") != CACHE_SCHEMA or not isinstance(entries, dict):
        return {}
    return {str(key): item for key, item in entries.items() if isinstance(item, dict)}


def refresh_kapt_facilities(
    dataset_path: str | Path = DEFAULT_DATASET,
    output_path: str | Path = DEFAULT_OUTPUT,
    *,
    today: str | None = None,
    max_age_days: int = 7,
    key: str | None = None,
    force: bool = False,
    checkpoint_every: int = 25,
    public_frame_path: str | Path | None = None,
    seed_paths: tuple[str | Path, ...] = (),
    seed_only: bool = False,
) -> dict[str, int | bool]:
    """Refresh identity-verified K-apt facilities for the current published pool.

    A stale verified entry survives a failed refresh and keeps its original observation date. New or
    previously rejected targets store only a fixed status code. This prevents a transient API outage
    from erasing already verified heating/parking facts.
    """
    if max_age_days < 1:
        raise ValueError("max_age_days must be positive")
    today_text = today or date.today().isoformat()
    today_date = date.fromisoformat(today_text)
    service_key = key if key is not None else os.environ.get("MOLIT_API_KEY", "")
    if not service_key:
        return {"changed": False, "targets": 0, "queried": 0, "verified": 0,
                "missing_key": True, "service_error": False}

    dataset = _read_json(Path(dataset_path), {})
    rows = dataset.get("complexes") if isinstance(dataset, dict) else None
    if not isinstance(rows, list):
        raise ValueError("KAPT_DATASET_INVALID")

    frame = _frame_index(public_frame_path)
    seeds = _seed_records(seed_paths)
    targets: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        complex_no = str(row.get("complex_no") or "").strip()
        identity = _identity(row)
        frame_row = frame.get(complex_no, {})
        identity["match_name"] = str(frame_row.get("match_name") or identity["name"])
        if not identity["units"] and frame_row.get("units"):
            identity["units"] = int(frame_row["units"])
        if not identity["built_year"] and frame_row.get("built_year"):
            identity["built_year"] = int(frame_row["built_year"])
        if complex_no and identity["name"] and identity["gu"]:
            targets.setdefault(complex_no, identity)

    output = Path(output_path)
    working = output.with_suffix(output.suffix + ".working")
    entries = _load_entries(working) or _load_entries(output)
    before = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    basis_cache: dict[str, dict | None] = {}
    queried = 0
    service_error = False

    for index, (complex_no, identity) in enumerate(targets.items(), 1):
        previous = entries.get(complex_no)
        recent_day = None
        if isinstance(previous, dict) and _same_target(previous, identity):
            recent_day = (
                previous.get("observed_date")
                if previous.get("kapt_verified") and previous.get("status") == "verified"
                else previous.get("last_attempt_date") or previous.get("observed_date")
            )
        if not force and isinstance(previous, dict) and _same_target(previous, identity) and _recent(recent_day, today_date, max_age_days):
            continue

        query_name = identity["match_name"].split("[")[0].strip()
        try:
            seed = seeds.get(complex_no)
            if seed_only or service_error:
                hit = None
            else:
                queried += 1
                clear_last_service_error()
                seeded_code = seed.get("kapt_code") if seed else None
                seeded_basis = (
                    _verified_seed_basis(seeded_code, identity, service_key, basis_cache)
                    if seeded_code else None
                )
                hit = (seeded_code, seeded_basis) if seeded_basis else _resolve_kapt_basis(
                    query_name, _district(identity["gu"]), identity["units"], identity["built_year"],
                    {}, service_key, basis_cache=basis_cache,
                )
                error_code = last_service_error_code()
                if not hit and error_code:
                    service_error = True
                    print(f"[kapt-facilities] API_ERROR_{error_code} — 남은 대상은 기존 검증값 유지")
        except Exception:  # API/파서 원문은 기록하지 않고 행 단위 실패로 격리한다.
            hit = None

        if hit:
            kapt_code, basis = hit
            entry = {
                **identity,
                "kapt_code": kapt_code,
                "kapt_verified": True,
                "status": "verified",
                "observed_date": today_text,
                "last_attempt_date": today_text,
                "source_name": SOURCE_NAME,
                "source_url": KAPT_DATASET_URL,
            }
            for field in FACILITY_FIELDS:
                if basis.get(field) is not None:
                    entry[field] = basis[field]
            entries[complex_no] = entry
        elif isinstance(previous, dict) and previous.get("kapt_verified") is True and _same_target(previous, identity):
            # 최신 조회가 실패해도 과거 검증 사실을 삭제하지 않는다. 관측일은 갱신하지 않아 UI가
            # 오래된 값임을 판별할 수 있고, 다음 날 다시 시도할 수 있게 시도일만 남긴다.
            entries[complex_no] = {**previous, "status": "refresh_failed", "last_attempt_date": today_text}
        elif seed:
            # 기존 overlay/universe의 코드는 과거 동일 신원게이트를 통과한 증거다. 최신 API가
            # 일시적으로 비어도 그 사실을 삭제하지 않고, 과거 관측일을 그대로 공개한다.
            entry = {
                **identity,
                "kapt_code": seed["kapt_code"],
                "kapt_verified": True,
                "status": "verified_cached",
                "observed_date": seed.get("observed_date"),
                "last_attempt_date": today_text,
                "source_name": SOURCE_NAME,
                "source_url": KAPT_DATASET_URL,
            }
            for field in FACILITY_FIELDS:
                if seed.get(field) is not None:
                    entry[field] = seed[field]
            entries[complex_no] = entry
        else:
            entries[complex_no] = {
                **identity,
                "kapt_verified": False,
                "status": "not_found_or_identity_rejected",
                "last_attempt_date": today_text,
            }

        if checkpoint_every and index % checkpoint_every == 0:
            _write_json_atomic(
                working,
                _cache_payload(entries, today=today_text, target_count=len(targets), complete=False),
            )
            verified_now = sum(1 for cno in targets if entries.get(cno, {}).get("kapt_verified") is True)
            print(f"[kapt-facilities] {index:,}/{len(targets):,} · 검증 {verified_now:,}")

    final_entries = {cno: entries[cno] for cno in targets if cno in entries}
    payload = _cache_payload(final_entries, today=today_text, target_count=len(targets), complete=True)
    after = json.dumps(final_entries, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    changed = before != after or not output.is_file()
    prior_entries = _load_entries(output)
    # 조사 범위 축소나 단지 신원 교체는 데이터 손실이 아니다. 직전 캐시 가운데 현재 공개 풀에
    # 남아 있고 동일 신원인 검증 행만 비교해야 실제 수집 회귀만 차단할 수 있다.
    prior_verified = sum(
        1
        for complex_no, entry in prior_entries.items()
        if (
            complex_no in targets
            and entry.get("kapt_verified") is True
            and _same_target(entry, targets[complex_no])
        )
    )
    seed_verified = sum(1 for complex_no in targets if complex_no in seeds)
    final_verified = sum(1 for entry in final_entries.values() if entry.get("kapt_verified") is True)
    if final_verified < max(prior_verified, seed_verified):
        raise RuntimeError("KAPT_COVERAGE_REGRESSION")
    if changed:
        _write_json_atomic(output, payload)
    try:
        working.unlink()
    except FileNotFoundError:
        pass

    verified = final_verified
    heating = sum(1 for entry in final_entries.values() if entry.get("heating"))
    parking = sum(1 for entry in final_entries.values() if entry.get("parking_per_unit") is not None)
    return {
        "changed": changed,
        "targets": len(targets),
        "queried": queried,
        "verified": verified,
        "heating": heating,
        "parking": parking,
        "missing_key": False,
        "service_error": service_error,
    }


def main() -> None:
    from agent_realestate import config

    config.load_env_file()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET))
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--today", default=date.today().isoformat())
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--seed-only", action="store_true",
                        help="API 장애 시 기존 신원검증 seed만으로 캐시를 안전 복원")
    scope_frames = sorted(glob.glob("report/public-scope/*/public-frame.json"))
    overlays = sorted(glob.glob("examples/enrich_overlay_*.json"))
    universes = sorted(glob.glob("examples/candidates_universe[0-9]*.json"))
    parser.add_argument("--public-frame", default=(scope_frames[-1] if scope_frames else ""))
    parser.add_argument("--seed-overlay", default=(overlays[-1] if overlays else ""))
    parser.add_argument("--seed-universe", default=(universes[-1] if universes else ""))
    args = parser.parse_args()
    stats = refresh_kapt_facilities(
        args.dataset,
        args.out,
        today=args.today,
        max_age_days=args.max_age_days,
        force=args.force,
        public_frame_path=args.public_frame,
        seed_paths=tuple(path for path in (args.seed_overlay, args.seed_universe) if path),
        seed_only=args.seed_only,
    )
    if stats["missing_key"]:
        print("[kapt-facilities] MOLIT_API_KEY MISSING — 기존 검증 캐시 유지")
        return
    print(
        "[kapt-facilities] "
        f"대상 {stats['targets']:,} · 조회 {stats['queried']:,} · K-apt 검증 {stats['verified']:,} · "
        f"난방 {stats['heating']:,} · 주차 {stats['parking']:,}"
    )


if __name__ == "__main__":
    main()
