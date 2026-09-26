"""Deterministic Hop-0 acquisition measurement contracts.

GSC, GA4/Tistory, and GEO observations stay as separate raw counts.  This
module intentionally does not infer causal lift or turn missing inputs into
zeros.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import date
import html
import json
import os
from pathlib import Path
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
        if any(value is not None and (not isinstance(value, int) or value < 0) for value in counts):
            raise ValueError("observation counts must be non-negative integers or null")
        if self.clicks is not None and self.impressions is not None and self.clicks > self.impressions:
            raise ValueError("clicks cannot exceed impressions")
        if self.average_position is not None and self.average_position <= 0:
            raise ValueError("average_position must be positive")

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
    return [AcquisitionObservation(**json.loads(line))
            for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


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
                   *, brand_terms: tuple[str, ...] = ()) -> list[AcquisitionObservation]:
    """Import a page×query×date GSC CSV without merging it with other telemetry."""
    validate_registry(intents)
    owner_by_path = {unquote(urlsplit(canonical_url(base_url, intent)).path): intent for intent in intents}
    groups: dict[tuple[str, str], dict[str, float]] = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = _gsc_columns(reader.fieldnames)
        for raw in reader:
            query = raw[columns["query"]].strip().casefold()
            if any(term.casefold() in query for term in brand_terms if term):
                continue
            page_path = unquote(urlsplit(raw[columns["page"]].strip()).path)
            intent = owner_by_path.get(page_path)
            if intent is None:
                continue
            observed_date = date.fromisoformat(raw[columns["date"]].strip()).isoformat()
            clicks = int(raw[columns["clicks"]].replace(",", ""))
            impressions = int(raw[columns["impressions"]].replace(",", ""))
            position = float(raw[columns["position"]])
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
        ))
    return observations


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

    readiness_parser = subparsers.add_parser(
        "readiness", help="Check whether a baseline can open the acquisition probe."
    )
    readiness_parser.add_argument("--observations", required=True)
    readiness_parser.add_argument("--required-urls", type=int, required=True)
    readiness_parser.add_argument("--min-impressions", type=int, required=True)
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
        )
        write_observations(args.out, observations)
        print(json.dumps({
            "status": "IMPORTED",
            "observations": len(observations),
            "output": str(args.out),
        }, ensure_ascii=False, sort_keys=True))
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
