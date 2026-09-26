"""Deterministic Hop-0 acquisition measurement contracts.

GSC, GA4/Tistory, and GEO observations stay as separate raw counts.  This
module intentionally does not infer causal lift or turn missing inputs into
zeros.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import html
import json
import math
import os
from pathlib import Path
import random
import tempfile
from urllib.parse import parse_qsl, urlencode, unquote, urlsplit, urlunsplit

from blog.search_intent import SearchIntent, canonical_url, validate_registry


_SOURCES = {"gsc", "ga4", "tistory", "geo"}


def campaign_url(url: str, *, source: str, medium: str, campaign_id: str, content_id: str) -> str:
    """Add stable attribution parameters while preserving unrelated query data."""
    if any(not value or "\n" in value for value in (source, medium, campaign_id, content_id)):
        raise ValueError("campaign attribution values must be non-empty single lines")
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.update({
        "utm_source": source,
        "utm_medium": medium,
        "utm_campaign": campaign_id,
        "utm_content": content_id,
    })
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def cta_event_attributes(intent: SearchIntent, surface: str) -> str:
    if surface not in {"owned_daily", "district", "complex"}:
        raise ValueError("unsupported CTA surface")
    payload = {
        "surface": surface,
        "intent_id": intent.intent_id,
        "campaign_id": intent.campaign_id,
        "content_id": intent.intent_id,
    }
    encoded = ",".join(f"{json.dumps(key)}:{json.dumps(value, ensure_ascii=False)}" for key, value in payload.items())
    script = f"try{{if(typeof gtag==='function')gtag('event','acquisition_cta_click',{{{encoded}}});}}catch(e){{}}"
    return f"data-intent-id=\"{html.escape(intent.intent_id)}\" data-campaign-id=\"{html.escape(intent.campaign_id)}\" onclick='{html.escape(script, quote=True)}'"


@dataclass(frozen=True)
class AcquisitionObservation:
    observed_date: str
    intent_id: str
    canonical_path: str
    source: str
    impressions: int | None = None
    clicks: int | None = None
    average_position: float | None = None
    landing_sessions: int | None = None
    engaged_sessions: int | None = None
    cta_events: int | None = None
    geo_citations: int | None = None
    indexed: bool | None = None
    nonbrand: bool | None = None
    provenance_path: str | None = None
    provenance_sha256: str | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    brand_terms: tuple[str, ...] = ()
    property_base_url: str | None = None
    import_scope_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        try:
            if date.fromisoformat(self.observed_date).isoformat() != self.observed_date:
                raise ValueError
        except ValueError as exc:
            raise ValueError("observed_date must be YYYY-MM-DD") from exc
        if not self.intent_id or not self.canonical_path.startswith("/") or self.source not in _SOURCES:
            raise ValueError("invalid acquisition observation identity")
        counts = (self.impressions, self.clicks, self.landing_sessions, self.engaged_sessions,
                  self.cta_events, self.geo_citations)
        if any(value is not None and (type(value) is not int or value < 0) for value in counts):
            raise ValueError("observation counts must be non-negative integers or null")
        if self.clicks is not None and self.impressions is not None and self.clicks > self.impressions:
            raise ValueError("clicks cannot exceed impressions")
        if self.average_position is not None and (type(self.average_position) not in {int, float}
                                                  or not math.isfinite(self.average_position) or self.average_position <= 0):
            raise ValueError("average_position must be positive")
        if any(value is not None and type(value) is not bool for value in (self.indexed, self.nonbrand)):
            raise ValueError("indexed/nonbrand must be explicit booleans or null")
        if (self.coverage_start is None) != (self.coverage_end is None):
            raise ValueError("export coverage needs both start and end dates")
        if self.coverage_start is not None:
            start, end = date.fromisoformat(self.coverage_start), date.fromisoformat(self.coverage_end)
            if start > end or not start <= date.fromisoformat(self.observed_date) <= end:
                raise ValueError("observation falls outside its declared export coverage")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ProbeReadiness:
    status: str
    eligible_urls: int
    required_urls: int
    reasons: tuple[str, ...]


def assess_gsc_readiness(observations: list[AcquisitionObservation], *, required_urls: int = 16,
                         min_impressions_per_url: int = 20) -> ProbeReadiness:
    if required_urls < 1 or min_impressions_per_url < 1:
        raise ValueError("readiness thresholds must be positive")
    gsc = [row for row in observations if row.source == "gsc"]
    by_intent: dict[str, dict[str, int | bool]] = {}
    for row in gsc:
        state = by_intent.setdefault(row.intent_id, {"impressions": 0, "indexed": False})
        state["impressions"] = int(state["impressions"]) + (row.impressions or 0)
        state["indexed"] = bool(state["indexed"]) or row.indexed is True
    eligible = sum(1 for state in by_intent.values()
                   if state["indexed"] and int(state["impressions"]) >= min_impressions_per_url)
    reasons = []
    if eligible < required_urls:
        reasons.append(f"eligible_urls={eligible}, need={required_urls}")
    status = "READY" if not reasons else "NOT_OPENED_INSUFFICIENT_BASELINE"
    return ProbeReadiness(status, eligible, required_urls, tuple(reasons))


def write_observations(path: str | os.PathLike[str], observations: list[AcquisitionObservation]) -> str:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row.as_dict(), ensure_ascii=False, sort_keys=True) + "\n" for row in observations)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, destination)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return str(destination)


def read_observations(path: str | os.PathLike[str]) -> list[AcquisitionObservation]:
    observations = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            raw = json.loads(line)
            if "brand_terms" in raw:
                raw["brand_terms"] = tuple(raw["brand_terms"])
            if "import_scope_paths" in raw:
                raw["import_scope_paths"] = tuple(raw["import_scope_paths"])
            observations.append(AcquisitionObservation(**raw))
    return observations


_GSC_ALIASES = {
    "date": ("date", "날짜"), "page": ("page", "페이지"), "query": ("query", "검색어"),
    "clicks": ("clicks", "클릭수"), "impressions": ("impressions", "노출수"),
    "position": ("position", "게재순위", "평균 게재순위"),
}


def _gsc_columns(fieldnames: list[str] | None) -> dict[str, str]:
    names = {name.strip().lower(): name for name in (fieldnames or [])}
    resolved = {}
    for key, aliases in _GSC_ALIASES.items():
        hit = next((names[alias.lower()] for alias in aliases if alias.lower() in names), None)
        if hit is None:
            raise ValueError(f"GSC export missing column: {key}")
        resolved[key] = hit
    return resolved


def import_gsc_csv(path: str | os.PathLike[str], intents: list[SearchIntent], base_url: str,
                   *, brand_terms: tuple[str, ...] = (), coverage_start: str | None = None,
                   coverage_end: str | None = None) -> list[AcquisitionObservation]:
    """Import a page×query×date GSC CSV without merging it with other telemetry."""
    validate_registry(intents)
    source_path = Path(path).resolve()
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    import_scope_paths = tuple(sorted(intent.canonical_path for intent in intents))
    expected_origin = urlsplit(base_url)
    owner_by_path = {unquote(urlsplit(canonical_url(base_url, intent)).path): intent for intent in intents}
    groups: dict[tuple[str, str], dict[str, float]] = {}
    seen = set()
    with source_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = _gsc_columns(reader.fieldnames)
        for raw in reader:
            raw_query = raw[columns["query"]].strip()
            query = raw_query.casefold()
            if any(term.casefold() in query for term in brand_terms if term):
                continue
            page = urlsplit(raw[columns["page"]].strip())
            if (page.scheme, page.netloc) != (expected_origin.scheme, expected_origin.netloc):
                continue
            page_path = unquote(page.path)
            intent = owner_by_path.get(page_path)
            if intent is None:
                continue
            observed_date = date.fromisoformat(raw[columns["date"]].strip()).isoformat()
            clicks = int(raw[columns["clicks"]].replace(",", ""))
            impressions = int(raw[columns["impressions"]].replace(",", ""))
            position = float(raw[columns["position"]])
            identity = (observed_date, intent.intent_id, raw_query)
            if identity in seen:
                raise ValueError("duplicate GSC page/query/date row")
            seen.add(identity)
            if clicks < 0 or impressions < 0 or clicks > impressions or not math.isfinite(position) or position <= 0:
                raise ValueError("invalid GSC raw counts/position")
            state = groups.setdefault((observed_date, intent.intent_id), {
                "clicks": 0, "impressions": 0, "weighted_position": 0.0,
            })
            state["clicks"] += clicks
            state["impressions"] += impressions
            state["weighted_position"] += position * impressions

    intent_by_id = {intent.intent_id: intent for intent in intents}
    observations = []
    for (observed_date, intent_id), state in sorted(groups.items()):
        impressions = int(state["impressions"])
        intent = intent_by_id[intent_id]
        observations.append(AcquisitionObservation(
            observed_date=observed_date, intent_id=intent_id, canonical_path=intent.canonical_path,
            source="gsc", impressions=impressions, clicks=int(state["clicks"]),
            average_position=(state["weighted_position"] / impressions if impressions else None),
            indexed=impressions > 0,
            nonbrand=bool(brand_terms), provenance_path=str(source_path), provenance_sha256=source_sha256,
            coverage_start=coverage_start, coverage_end=coverage_end,
            brand_terms=brand_terms, property_base_url=base_url,
            import_scope_paths=import_scope_paths,
        ))
    return observations


def _replay_gsc_export(row: AcquisitionObservation) -> dict[tuple[str, str], dict]:
    """Replay raw counts using the recorded property and nonbrand filter, not hand-edited JSONL."""
    if not row.property_base_url or not row.brand_terms or not any(term.strip() for term in row.brand_terms):
        raise ValueError("probe needs the raw export property and nonbrand filter")
    if not row.import_scope_paths or len(set(row.import_scope_paths)) != len(row.import_scope_paths):
        raise ValueError("probe needs the original registry import scope; reimport the raw GSC CSV")
    scope_paths = {unquote(path) for path in row.import_scope_paths}
    origin = urlsplit(row.property_base_url)
    prefix = unquote(origin.path).rstrip("/")
    groups = {}
    seen = set()
    with Path(row.provenance_path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = _gsc_columns(reader.fieldnames)
        for raw in reader:
            query = raw[columns["query"]].strip()
            if any(term.casefold() in query.casefold() for term in row.brand_terms if term):
                continue
            page = urlsplit(raw[columns["page"]].strip())
            page_path = unquote(page.path)
            if (page.scheme, page.netloc) != (origin.scheme, origin.netloc) or not page_path.startswith(prefix + "/"):
                continue
            canonical_path = page_path[len(prefix):]
            if canonical_path not in scope_paths:
                continue
            observed = date.fromisoformat(raw[columns["date"]].strip()).isoformat()
            if not row.coverage_start <= observed <= row.coverage_end:
                raise ValueError("raw GSC export contains a date outside its declared inclusive window")
            identity = (observed, page_path, query)
            if identity in seen:
                raise ValueError("duplicate raw GSC page/query/date record")
            seen.add(identity)
            clicks = int(raw[columns["clicks"]].replace(",", ""))
            impressions = int(raw[columns["impressions"]].replace(",", ""))
            position = float(raw[columns["position"]])
            if clicks < 0 or impressions < 0 or clicks > impressions or not math.isfinite(position) or position <= 0:
                raise ValueError("invalid GSC raw counts/position")
            state = groups.setdefault((observed, canonical_path), {"clicks": 0, "impressions": 0, "weighted": 0.0})
            state["clicks"] += clicks
            state["impressions"] += impressions
            state["weighted"] += position * impressions
    return groups


def _window_totals(observations: list[AcquisitionObservation], start: str, end: str,
                   *, strict: bool = True) -> dict[str, dict]:
    """Missing records remain missing; explicit complete export coverage is required."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    groups: dict[str, dict] = {}
    seen = set()
    checked_sources = set()
    replayed_sources = {}
    replayed_observations = {}
    for row in observations:
        if row.source != "gsc" or not first <= date.fromisoformat(row.observed_date) <= last:
            continue
        identity = (row.intent_id, row.observed_date)
        if identity in seen:
            raise ValueError("duplicate GSC owner/date observations; overlapping imports are not additive")
        seen.add(identity)
        if strict:
            if row.nonbrand is not True or row.impressions is None:
                raise ValueError("probe needs explicit nonbrand GSC counts")
            if row.coverage_start != start or row.coverage_end != end:
                raise ValueError("probe needs an export covering the exact inclusive 28-day window")
            if not row.provenance_path or not row.provenance_sha256:
                raise ValueError("probe needs preserved raw GSC export provenance")
            source = (row.provenance_path, row.provenance_sha256)
            if source not in checked_sources:
                path = Path(row.provenance_path)
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != row.provenance_sha256:
                    raise ValueError("raw GSC export is missing or changed")
                checked_sources.add(source)
            replay_key = (row.provenance_path, row.provenance_sha256, row.property_base_url, tuple(row.brand_terms))
            if replay_key not in replayed_sources:
                replayed_sources[replay_key] = _replay_gsc_export(row)
                replayed_observations[replay_key] = {"scope": row.import_scope_paths, "keys": set()}
            if row.import_scope_paths != replayed_observations[replay_key]["scope"]:
                raise ValueError("one raw GSC export has inconsistent registry import scopes")
            replayed_observations[replay_key]["keys"].add((row.observed_date, unquote(row.canonical_path)))
            raw_state = replayed_sources[replay_key].get((row.observed_date, unquote(row.canonical_path)))
            if raw_state is None:
                raise ValueError("GSC observation has no corresponding raw page/date record")
            position = raw_state["weighted"] / raw_state["impressions"] if raw_state["impressions"] else None
            if (row.impressions != raw_state["impressions"] or row.clicks != raw_state["clicks"]
                    or row.indexed != (raw_state["impressions"] > 0)
                    or (position is None) != (row.average_position is None)
                    or position is not None and not math.isclose(position, row.average_position, rel_tol=1e-12)):
                raise ValueError("GSC observation counts/position do not match the preserved raw export")
        state = groups.setdefault(row.intent_id, {"impressions": 0, "indexed": False,
                                                  "weighted_position": 0.0, "position_impressions": 0,
                                                  "canonical_path": row.canonical_path})
        if state["canonical_path"] != row.canonical_path:
            raise ValueError("one observation intent has multiple canonical paths")
        state["impressions"] += row.impressions or 0
        state["indexed"] = state["indexed"] or row.indexed is True
        if row.average_position is not None:
            state["weighted_position"] += row.average_position * (row.impressions or 0)
            state["position_impressions"] += row.impressions or 0
    for replay_key, raw_states in replayed_sources.items():
        missing = raw_states.keys() - replayed_observations[replay_key]["keys"]
        if missing:
            raise ValueError(f"GSC import is incomplete: missing {len(missing)} raw page/date observations")
    for state in groups.values():
        denominator = state.pop("position_impressions")
        numerator = state.pop("weighted_position")
        state["average_position"] = numerator / denominator if denominator else None
    return groups


def prepare_acquisition_probe(intents: list[SearchIntent], observations: list[AcquisitionObservation],
                              dataset: dict, *, probe_id: str, d0: str, deployment_date: str,
                              deployment_id: str, seed: int) -> dict:
    """사전등록 가능한 8쌍을 배정한다. 부족한 실제 baseline은 NOT_OPENED다."""
    from blog.complex_page import complex_slug, select_page_complexes

    validate_registry(intents)
    start = date.fromisoformat(d0)
    deployment = date.fromisoformat(deployment_date)
    if any(not value or "\n" in value for value in (probe_id, deployment_id)):
        raise ValueError("probe_id/deployment_id must be nonempty single lines")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    pre_start, pre_end = (start - timedelta(days=28)).isoformat(), (start - timedelta(days=1)).isoformat()
    base = {"schema": "AcquisitionProbe/v1", "probe_id": probe_id, "d0": d0,
            "deployment_date": deployment_date, "deployment_id": deployment_id, "seed": seed,
            "baseline_start": pre_start, "baseline_end": pre_end,
            "post_start": d0, "post_end": (start + timedelta(days=27)).isoformat(),
            "evaluation_date": (start + timedelta(days=28)).isoformat(),
            "guardrail_dates": [(start + timedelta(days=days)).isoformat() for days in (7, 14)],
            "rules": {"required_pairs": 8, "min_pre_impressions_per_url": 20,
                      "min_post_impressions_total": 320, "min_post_impressions_per_pair": 40,
                      "won_pairs": 6, "killed_pairs_max": 3},
            "interpretation": "IgnitionProbe resource decision; no statistical or causal generalization"}
    if deployment >= date.fromisoformat(pre_start):
        return {**base, "status": "NOT_OPENED_INSUFFICIENT_BASELINE", "eligible_urls": 0,
                "reasons": ["28-day baseline must start after the latest visual/content deployment calendar date"], "pairs": []}
    totals = _window_totals(observations, pre_start, pre_end)
    allowed = {f"complex:{complex_slug(row['gu'], row['name'])}": row for row in select_page_complexes(dataset)}
    candidates = []
    for intent in intents:
        state = totals.get(intent.intent_id)
        row = allowed.get(intent.intent_id)
        if intent.entity_type != "complex" or not state or row is None:
            continue
        if state["canonical_path"] != intent.canonical_path:
            raise ValueError("baseline canonical does not match registry owner")
        if state["indexed"] and state["impressions"] >= 20 and state["average_position"] is not None:
            candidates.append({"intent_id": intent.intent_id, "canonical_path": intent.canonical_path,
                               "entity_type": intent.entity_type, "gu": row["gu"],
                               "area_band": "40-69" if row["area_m2"] < 70 else "70-99" if row["area_m2"] < 100 else "100+",
                               "pre_impressions": state["impressions"], "pre_position": state["average_position"]})
    if len(candidates) < 16:
        return {**base, "status": "NOT_OPENED_INSUFFICIENT_BASELINE", "eligible_urls": len(candidates),
                "reasons": [f"eligible_urls={len(candidates)}, need=16"], "pairs": []}
    eligible_count = len(candidates)
    candidates.sort(key=lambda item: (-item["pre_impressions"], item["intent_id"]))
    randomizer = random.Random(seed)
    pairs = []
    while len(candidates) >= 2 and len(pairs) < 8:
        first = candidates.pop(0)
        nearest = min(range(len(candidates)), key=lambda index: (
            candidates[index]["area_band"] != first["area_band"],
            abs(math.log((candidates[index]["pre_impressions"] + 1) / (first["pre_impressions"] + 1)))
            + abs(candidates[index]["pre_position"] - first["pre_position"]) / 10,
            candidates[index]["gu"] != first["gu"], candidates[index]["intent_id"]))
        second = candidates.pop(nearest)
        treatment, control = (first, second) if randomizer.randrange(2) else (second, first)
        pairs.append({"pair_id": f"pair-{len(pairs) + 1:02d}", "treatment": treatment, "control": control})
    registry_body = json.dumps([intent.as_dict() for intent in intents], ensure_ascii=False, sort_keys=True)
    return {**base, "status": "PREREGISTERED", "eligible_urls": eligible_count, "pairs": pairs,
            "registry_sha256": hashlib.sha256(registry_body.encode()).hexdigest(),
            "baseline_observations_sha256": hashlib.sha256(json.dumps([row.as_dict() for row in observations], sort_keys=True).encode()).hexdigest(),
            "provenance": sorted({(row.provenance_path, row.provenance_sha256) for row in observations
                                  if row.source == "gsc" and pre_start <= row.observed_date <= pre_end}),
            "treatment": "intent title/description + factual answer + source/date + related hub; all other features frozen",
            "falsifier": "canonical/indexability/schema/source/wording regression or concurrent content/visual change stops the probe"}


def write_probe(path: str | Path, probe: dict) -> str:
    """A preregistration is immutable. A new deployment needs a new probe id/file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(probe, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        os.unlink(temporary)
    return str(destination)


def check_probe_guardrail(probe: dict, *, checked_date: str, audit: dict,
                          latest_deployment_date: str, latest_deployment_id: str, facts_valid: bool,
                          publisher_valid: bool, wording_valid: bool, cohort_validation: dict | None = None) -> dict:
    checked = date.fromisoformat(checked_date)
    if checked < date.fromisoformat(probe["d0"]):
        raise ValueError("guardrail cannot run before D0")
    reasons = []
    if probe.get("status") != "PREREGISTERED":
        reasons.append("probe was not preregistered")
    if latest_deployment_date != probe["deployment_date"] or latest_deployment_id != probe["deployment_id"]:
        reasons.append("deployment changed; restart baseline and preregister a new probe")
    if audit.get("schema") != "SeoGeoAudit/v1" or audit.get("status") != "PASS":
        reasons.append("technical SEO/schema guardrail failed")
    if (not cohort_validation or cohort_validation.get("schema") != "AcquisitionCohortCheck/v1"
            or cohort_validation.get("probe_id") != probe["probe_id"] or cohort_validation.get("status") != "PASS"):
        reasons.append("frozen cohort payload evidence missing or changed")
    if not facts_valid or not publisher_valid or not wording_valid:
        reasons.append("facts/source/wording/publisher guardrail failed")
    return {"schema": "AcquisitionGuardrail/v1", "probe_id": probe["probe_id"],
            "checked_date": checked_date, "status": "STOP" if reasons else "CONTINUE",
            "reasons": reasons, "outcome_judged": False}


def build_treatment_overlay(probe: dict) -> dict[str, dict]:
    """Only the eight preregistered treatment owners receive the answer block."""
    if probe.get("schema") != "AcquisitionProbe/v1" or probe.get("status") != "PREREGISTERED":
        raise ValueError("treatment requires a completed preregistration")
    if len(probe.get("pairs", [])) != 8:
        raise ValueError("treatment requires eight complete matched pairs")
    expected_rules = {"required_pairs": 8, "min_pre_impressions_per_url": 20,
                      "min_post_impressions_total": 320, "min_post_impressions_per_pair": 40,
                      "won_pairs": 6, "killed_pairs_max": 3}
    if probe.get("rules") != expected_rules:
        raise ValueError("preregistered probe rules were changed")
    start = date.fromisoformat(probe["d0"])
    windows = {"baseline_start": (start - timedelta(days=28)).isoformat(),
               "baseline_end": (start - timedelta(days=1)).isoformat(),
               "post_start": start.isoformat(), "post_end": (start + timedelta(days=27)).isoformat(),
               "evaluation_date": (start + timedelta(days=28)).isoformat()}
    if any(probe.get(field) != value for field, value in windows.items()):
        raise ValueError("preregistered inclusive date windows were changed")
    overlay = {}
    owners = set()
    canonicals = set()
    for pair in probe["pairs"]:
        for arm in ("treatment", "control"):
            owner = pair[arm]
            if owner["intent_id"] in owners or owner["canonical_path"] in canonicals:
                raise ValueError("probe owner is assigned more than once")
            if (owner.get("entity_type") != "complex" or not owner["intent_id"].startswith("complex:")
                    or type(owner.get("pre_impressions")) is not int or owner["pre_impressions"] < 20
                    or not math.isfinite(owner.get("pre_position", 0)) or owner.get("pre_position", 0) <= 0):
                raise ValueError("registered owner is not an eligible baseline complex")
            owners.add(owner["intent_id"])
            canonicals.add(owner["canonical_path"])
        treatment = pair["treatment"]
        overlay[treatment["intent_id"]] = {"probe_id": probe["probe_id"], "d0": probe["d0"],
                                               "canonical_path": treatment["canonical_path"],
                                               "kind": "factual-answer"}
    return overlay


def freeze_probe_cohort(probe: dict, intents: list[SearchIntent], site_dir: str | Path,
                        base_url: str, cohort_path: str | Path, *, frozen_date: str) -> dict:
    """D0 처리 HTML과 대조 HTML 16개를 보존한다. 일간 재생성에서 동일 payload를 복구한다."""
    from blog.seo_geo_audit import ParsedPage, _local_path

    overlay = build_treatment_overlay(probe)
    if frozen_date != probe["d0"]:
        raise ValueError("the cohort must be frozen on D0")
    registry = {intent.intent_id: intent for intent in intents}
    root = Path(site_dir).resolve()
    manifest_path = Path(cohort_path).resolve()
    if manifest_path.exists():
        raise FileExistsError("cohort manifest is immutable")
    snapshot_dir = manifest_path.parent / (manifest_path.stem + "-html")
    owners = []
    payloads = []
    for pair in probe["pairs"]:
        for arm in ("treatment", "control"):
            assigned = pair[arm]
            intent = registry.get(assigned["intent_id"])
            if intent is None or intent.canonical_path != assigned["canonical_path"]:
                raise ValueError("cohort registry owner changed")
            target = _local_path(root, canonical_url(base_url, intent), base_url)
            if target is None or not target.is_file():
                raise ValueError("cohort owner is not generated")
            raw = target.read_bytes()
            page = ParsedPage(raw.decode("utf-8"))
            if page.canonicals != [canonical_url(base_url, intent)]:
                raise ValueError("cohort canonical changed")
            marker = f'data-acquisition-probe="{html.escape(probe["probe_id"], quote=True)}"'
            if (arm == "treatment") != (marker in raw.decode("utf-8")):
                raise ValueError("treatment/control HTML does not match the registered assignment")
            filename = hashlib.sha256(intent.intent_id.encode()).hexdigest()[:24] + ".html"
            snapshot = snapshot_dir / filename
            owners.append({"intent": intent.as_dict(), "arm": arm, "snapshot_path": str(snapshot),
                           "payload_sha256": hashlib.sha256(raw).hexdigest()})
            payloads.append((snapshot, raw))
    snapshot_dir.mkdir(parents=True, exist_ok=False)
    for snapshot, raw in payloads:
        with snapshot.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    cohort = {"schema": "AcquisitionCohort/v1", "probe_id": probe["probe_id"],
              "d0": probe["d0"], "baseline_deployment_id": probe["deployment_id"],
              "base_url": base_url, "owners": owners, "treatment_owners": sorted(overlay),
              "preregistration_sha256": hashlib.sha256(json.dumps(probe, sort_keys=True).encode()).hexdigest()}
    write_probe(manifest_path, cohort)
    return cohort


def validate_probe_cohort(cohort: dict, site_dir: str | Path, base_url: str) -> dict:
    from blog.seo_geo_audit import _local_path

    if cohort.get("schema") != "AcquisitionCohort/v1" or cohort.get("base_url") != base_url or len(cohort.get("owners", [])) != 16:
        raise ValueError("invalid frozen acquisition cohort")
    failures = []
    identities = set()
    for owner in cohort["owners"]:
        intent = owner["intent"]
        if intent["intent_id"] in identities:
            raise ValueError("duplicate frozen owner")
        identities.add(intent["intent_id"])
        path = _local_path(Path(site_dir).resolve(), base_url.rstrip("/") + intent["canonical_path"], base_url)
        snapshot = Path(owner["snapshot_path"])
        for label, candidate in (("snapshot", snapshot), ("generated", path)):
            if candidate is None or not candidate.is_file() or hashlib.sha256(candidate.read_bytes()).hexdigest() != owner["payload_sha256"]:
                failures.append({"intent_id": intent["intent_id"], "kind": label, "code": "PAYLOAD_CHANGED"})
    return {"schema": "AcquisitionCohortCheck/v1", "probe_id": cohort["probe_id"],
            "status": "PASS" if not failures else "FAIL", "checked_owners": len(identities), "failures": failures}


def restore_probe_cohort(cohort_path: str | Path, site_dir: str | Path, base_url: str,
                         intents: list[SearchIntent], *, today: str) -> list[SearchIntent]:
    """Daily build hook: restore 16 frozen payloads and their exact D0 registry fields."""
    from blog.seo_geo_audit import _local_path

    cohort = json.loads(Path(cohort_path).read_text(encoding="utf-8"))
    if date.fromisoformat(today) < date.fromisoformat(cohort["d0"]):
        raise ValueError("cannot restore a cohort before D0")
    validate_probe_cohort(cohort, site_dir, base_url)  # validates snapshot and schema; live differences are expected here
    registry = {intent.intent_id: intent for intent in intents}
    writes = []
    for owner in cohort["owners"]:
        raw_intent = {**owner["intent"], "entity_ids": tuple(owner["intent"]["entity_ids"]),
                      "answer_claim_ids": tuple(owner["intent"]["answer_claim_ids"])}
        intent = SearchIntent(**raw_intent)
        if intent.intent_id not in registry or registry[intent.intent_id].canonical_path != intent.canonical_path:
            raise ValueError("daily evidence gate removed a frozen owner; stop the probe")
        snapshot = Path(owner["snapshot_path"])
        raw = snapshot.read_bytes()
        if hashlib.sha256(raw).hexdigest() != owner["payload_sha256"]:
            raise ValueError("frozen cohort snapshot changed")
        target = _local_path(Path(site_dir).resolve(), canonical_url(base_url, intent), base_url)
        if target is None:
            raise ValueError("frozen owner escapes site root")
        writes.append((target, raw))
        registry[intent.intent_id] = intent
    for target, raw in writes:
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    result = [registry[intent.intent_id] for intent in intents]
    validate_registry(result)
    return result


def evaluate_acquisition_probe(probe: dict, observations: list[AcquisitionObservation],
                               *, evaluated_date: str, guardrails: list[dict]) -> dict:
    """D28는 D0~D27의 28일 데이터를 평가한다. 누락 URL은 0으로 채우지 않는다."""
    result = {"schema": "AcquisitionOutcome/v1", "probe_id": probe["probe_id"],
              "evaluated_date": evaluated_date, "interpretation": probe["interpretation"]}
    if probe.get("status") != "PREREGISTERED":
        return {**result, "status": "NOT_EXECUTED", "reasons": probe.get("reasons", [])}
    build_treatment_overlay(probe)  # recheck frozen rule, window and owner contracts
    if date.fromisoformat(evaluated_date) < date.fromisoformat(probe["evaluation_date"]):
        return {**result, "status": "NOT_DUE", "reasons": ["D28 has not elapsed"]}
    guardrail_by_date = {item["checked_date"]: item for item in guardrails if item.get("probe_id") == probe["probe_id"]}
    if any(item.get("status") == "STOP" for item in guardrails if item.get("probe_id") == probe["probe_id"]):
        return {**result, "status": "STOPPED", "reasons": ["recorded guardrail failure"]}
    required_dates = [*probe["guardrail_dates"], evaluated_date]
    if any(guardrail_by_date.get(day, {}).get("status") != "CONTINUE" for day in required_dates):
        return {**result, "status": "INCONCLUSIVE", "reasons": ["missing D7/D14/evaluation guardrail evidence"]}
    totals = _window_totals(observations, probe["post_start"], probe["post_end"])
    pairs = []
    reasons = []
    for pair in probe["pairs"]:
        t, c = pair["treatment"], pair["control"]
        tpost, cpost = totals.get(t["intent_id"]), totals.get(c["intent_id"])
        if tpost is None or cpost is None:
            reasons.append(f"{pair['pair_id']}: missing post observations")
            continue
        if tpost["canonical_path"] != t["canonical_path"] or cpost["canonical_path"] != c["canonical_path"]:
            raise ValueError("post canonical changed from preregistration")
        effect = math.log((tpost["impressions"] + 1) / (t["pre_impressions"] + 1)) - math.log((cpost["impressions"] + 1) / (c["pre_impressions"] + 1))
        pair_count = tpost["impressions"] + cpost["impressions"]
        pairs.append({"pair_id": pair["pair_id"], "t_pre": t["pre_impressions"], "c_pre": c["pre_impressions"],
                      "t_post": tpost["impressions"], "c_post": cpost["impressions"], "pair_effect": effect,
                      "treatment_won": effect > 0})
        if pair_count < 40:
            reasons.append(f"{pair['pair_id']}: post impressions {pair_count} < 40")
    count = sum(item["t_post"] + item["c_post"] for item in pairs)
    if len(pairs) != 8 or count < 320:
        reasons.append(f"complete_pairs={len(pairs)}/8, post_impressions={count}, need=320")
    won = sum(item["treatment_won"] for item in pairs)
    aggregate = None
    if len(pairs) == 8:
        aggregate = math.log((sum(p["t_post"] for p in pairs) + 1) / (sum(p["t_pre"] for p in pairs) + 1)) - math.log((sum(p["c_post"] for p in pairs) + 1) / (sum(p["c_pre"] for p in pairs) + 1))
    status = "INCONCLUSIVE" if reasons else "WON" if won >= 6 and aggregate > 0 else "KILLED" if won <= 3 and aggregate <= 0 else "INCONCLUSIVE"
    return {**result, "status": status, "reasons": reasons, "completed_pairs": len(pairs),
            "treatment_won_pairs": won, "post_impressions": count, "aggregate_effect": aggregate,
            "pairs": pairs, "statistical_significance_claimed": False}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import and assess owned-search acquisition observations."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser(
        "import-gsc", help="Import a page×query×date Search Console CSV."
    )
    import_parser.add_argument("--csv", required=True)
    import_parser.add_argument("--registry", required=True)
    import_parser.add_argument("--base-url", required=True)
    import_parser.add_argument("--out", required=True)
    import_parser.add_argument("--brand-term", action="append", default=[])
    import_parser.add_argument("--period-start")
    import_parser.add_argument("--period-end")

    readiness_parser = subparsers.add_parser(
        "readiness", help="Check whether a baseline can open the acquisition probe."
    )
    readiness_parser.add_argument("--observations", required=True)
    readiness_parser.add_argument("--required-urls", type=int, required=True)
    readiness_parser.add_argument("--min-impressions", type=int, required=True)
    prepare = subparsers.add_parser("preregister", help="Freeze an eight-pair assignment or record insufficient baseline.")
    prepare.add_argument("--observations", required=True)
    prepare.add_argument("--registry", required=True)
    prepare.add_argument("--dataset", required=True)
    prepare.add_argument("--probe-id", required=True)
    prepare.add_argument("--d0", required=True)
    prepare.add_argument("--deployment-date", required=True)
    prepare.add_argument("--deployment-id", required=True)
    prepare.add_argument("--seed", type=int, required=True)
    prepare.add_argument("--out", required=True)
    guard = subparsers.add_parser("guardrail", help="Record D7/D14 technical/fact guardrails without judging growth.")
    guard.add_argument("--probe", required=True)
    guard.add_argument("--audit", required=True)
    guard.add_argument("--checked-date", required=True)
    guard.add_argument("--deployment-date", required=True)
    guard.add_argument("--deployment-id", required=True)
    guard.add_argument("--cohort", required=True)
    guard.add_argument("--site", required=True)
    guard.add_argument("--base-url", required=True)
    for field in ("facts", "publisher", "wording"):
        guard.add_argument(f"--{field}-valid", choices=("yes", "no"), required=True)
    guard.add_argument("--out", required=True)
    evaluate = subparsers.add_parser("evaluate", help="D28 resource decision; missing evidence stays inconclusive.")
    evaluate.add_argument("--probe", required=True)
    evaluate.add_argument("--observations", required=True)
    evaluate.add_argument("--guardrail", action="append", required=True)
    evaluate.add_argument("--evaluated-date", required=True)
    evaluate.add_argument("--out", required=True)
    overlay = subparsers.add_parser("overlay", help="Write the eight treatment-only owner overlays.")
    overlay.add_argument("--probe", required=True)
    overlay.add_argument("--out", required=True)
    freeze = subparsers.add_parser("freeze-cohort", help="Freeze the D0 treatment/control HTML and registry rows.")
    freeze.add_argument("--probe", required=True)
    freeze.add_argument("--registry", required=True)
    freeze.add_argument("--site", required=True)
    freeze.add_argument("--base-url", required=True)
    freeze.add_argument("--frozen-date", required=True)
    freeze.add_argument("--out", required=True)
    cohort_check = subparsers.add_parser("cohort-check", help="Verify frozen snapshots and live 16-owner payload hashes.")
    cohort_check.add_argument("--cohort", required=True)
    cohort_check.add_argument("--site", required=True)
    cohort_check.add_argument("--base-url", required=True)
    cohort_check.add_argument("--out", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "import-gsc":
        from blog.search_intent import read_intent_registry

        intents = read_intent_registry(args.registry)
        observations = import_gsc_csv(
            args.csv,
            intents=intents,
            base_url=args.base_url,
            brand_terms=tuple(args.brand_term),
            coverage_start=args.period_start, coverage_end=args.period_end,
        )
        write_observations(args.out, observations)
        print(json.dumps({
            "status": "IMPORTED",
            "observations": len(observations),
            "output": str(args.out),
        }, ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "preregister":
        from blog.search_intent import read_intent_registry

        probe = prepare_acquisition_probe(
            read_intent_registry(args.registry), read_observations(args.observations),
            json.loads(Path(args.dataset).read_text(encoding="utf-8")), probe_id=args.probe_id,
            d0=args.d0, deployment_date=args.deployment_date, deployment_id=args.deployment_id, seed=args.seed)
        write_probe(args.out, probe)
        print(json.dumps({key: value for key, value in probe.items() if key not in {"pairs", "provenance"}}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.command == "freeze-cohort":
        from blog.search_intent import read_intent_registry

        result = freeze_probe_cohort(json.loads(Path(args.probe).read_text(encoding="utf-8")),
                                    read_intent_registry(args.registry), args.site, args.base_url,
                                    args.out, frozen_date=args.frozen_date)
        print(json.dumps({"status": "COHORT_FROZEN", "probe_id": result["probe_id"], "owners": len(result["owners"]), "output": args.out}))
        return 0
    if args.command == "cohort-check":
        result = validate_probe_cohort(json.loads(Path(args.cohort).read_text(encoding="utf-8")), args.site, args.base_url)
        write_probe(args.out, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0 if result["status"] == "PASS" else 1
    if args.command in {"guardrail", "evaluate", "overlay"}:
        probe = json.loads(Path(args.probe).read_text(encoding="utf-8"))
        if args.command == "guardrail":
            result = check_probe_guardrail(probe, checked_date=args.checked_date,
                                          audit=json.loads(Path(args.audit).read_text(encoding="utf-8")),
                                          latest_deployment_date=args.deployment_date,
                                          latest_deployment_id=args.deployment_id,
                                          cohort_validation=validate_probe_cohort(json.loads(Path(args.cohort).read_text(encoding="utf-8")), args.site, args.base_url),
                                          facts_valid=args.facts_valid == "yes", publisher_valid=args.publisher_valid == "yes",
                                          wording_valid=args.wording_valid == "yes")
        elif args.command == "overlay":
            result = {"schema": "AcquisitionOverlay/v1", "probe_id": probe["probe_id"], "owners": build_treatment_overlay(probe)}
        else:
            result = evaluate_acquisition_probe(probe, read_observations(args.observations),
                                                evaluated_date=args.evaluated_date,
                                                guardrails=[json.loads(Path(path).read_text(encoding="utf-8")) for path in args.guardrail])
        write_probe(args.out, result)
        print(json.dumps({key: value for key, value in result.items() if key not in {"pairs", "owners"}}, ensure_ascii=False, sort_keys=True))
        return 0

    observations = read_observations(args.observations)
    readiness = assess_gsc_readiness(
        observations,
        required_urls=args.required_urls,
        min_impressions=args.min_impressions,
    )
    print(json.dumps(asdict(readiness), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
