"""실재 한국어 URL의 GEO 질의·인용 관측 원장. 미관측은 0이 아니다."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import ipaddress
import json
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit

from blog.search_intent import SearchIntent, canonical_url, read_intent_registry, validate_registry
from blog.seo_geo_audit import ParsedPage, _local_path


ENGINES = ("chatgpt", "perplexity", "google_ai")


def _public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("GEO URLs must be public HTTPS URLs without credentials")
    if parsed.hostname == "localhost" or parsed.hostname.endswith((".local", ".internal")):
        raise ValueError("local/private GEO targets are forbidden")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("local/private GEO targets are forbidden")


@dataclass(frozen=True)
class GeoQuery:
    query_id: str
    intent_id: str
    query: str
    locale: str
    target_url: str
    selection_basis: str

    def __post_init__(self) -> None:
        if self.locale != "ko-KR" or not re.search(r"[가-힣]", self.query):
            raise ValueError("active GEO query and target locale must be ko-KR")
        if not self.query_id or not self.intent_id or not self.selection_basis or "\n" in self.query:
            raise ValueError("GEO query identity is incomplete")
        _public_url(self.target_url)


def build_geo_queries(intents: list[SearchIntent], site_dir: str | Path, base_url: str,
                      *, count: int = 20, dataset: dict | None = None) -> list[GeoQuery]:
    """Use live-generated owners; selection by sample coverage is not search-demand ranking."""
    validate_registry(intents)
    if count < 1 or len(intents) < count:
        raise ValueError("not enough real canonical owners for the requested query count")
    _public_url(base_url)
    row_by_entity = {(row["gu"], row["name"]): row for row in (dataset or {}).get("complexes", [])}
    by_gu: Counter = Counter()
    for (gu, _name), row in row_by_entity.items():
        by_gu[gu] += row.get("molit_n") or 0

    def sample_rank(intent: SearchIntent) -> tuple:
        if intent.entity_type == "complex":
            samples = row_by_entity.get(tuple(intent.entity_ids), {}).get("molit_n") or 0
        elif intent.entity_type == "district":
            samples = by_gu[intent.entity_ids[0]]
        else:
            samples = 0
        return (-samples, intent.intent_id)

    daily = sorted((i for i in intents if i.entity_type == "daily"), key=lambda i: i.intent_id)
    districts = sorted((i for i in intents if i.entity_type == "district"), key=sample_rank)
    complexes = sorted((i for i in intents if i.entity_type == "complex"), key=sample_rank)
    # The first standard 20-query panel spans the daily page, nine districts, ten complexes.
    selected = daily[:1] + districts[: min(9, max(0, count - len(daily[:1])))]
    selected += complexes[: max(0, count - len(selected))]
    selected_ids = {intent.intent_id for intent in selected}
    selected += [intent for intent in sorted(intents, key=sample_rank) if intent.intent_id not in selected_ids][: max(0, count - len(selected))]
    queries = []
    for intent in selected[:count]:
        url = canonical_url(base_url, intent)
        local = _local_path(Path(site_dir).resolve(), url, base_url)
        if local is None or not local.is_file():
            raise ValueError(f"GEO phantom target: {intent.intent_id}")
        page = ParsedPage(local.read_text(encoding="utf-8"))
        if page.canonicals != [url] or page.lang.lower() not in {"ko", "ko-kr"}:
            raise ValueError(f"GEO target canonical/locale mismatch: {intent.intent_id}")
        query = f"{intent.target_query}를 확인할 수 있는 공공데이터와 최근 관측 기준일은 무엇인가요?"
        query_id = "re-" + hashlib.sha256(intent.intent_id.encode()).hexdigest()[:16]
        queries.append(GeoQuery(query_id, intent.intent_id, query, "ko-KR", url,
                                "canonical coverage; transaction sample coverage within entity type, not measured search demand"))
    return queries


def write_query_panel(path: str | Path, queries: list[GeoQuery]) -> str:
    if len({query.query_id for query in queries}) != len(queries):
        raise ValueError("duplicate GEO query ids")
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(asdict(query), ensure_ascii=False, sort_keys=True) + "\n" for query in queries), encoding="utf-8")
    return str(destination)


def read_query_panel(path: str | Path) -> list[GeoQuery]:
    queries = [GeoQuery(**json.loads(line)) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({query.query_id for query in queries}) != len(queries):
        raise ValueError("duplicate GEO query ids")
    return queries


@dataclass(frozen=True)
class GeoObservation:
    run_id: str
    query_id: str
    engine: str
    mode: str
    queried_at: str
    locale: str
    evidence_path: str
    evidence_sha256: str
    cited_urls: tuple[str, ...]
    mentioned: bool
    recommended: bool
    recommendation_excerpt: str = ""
    region: str = "unknown"
    model: str = "unknown"
    response_id: str | None = None
    conversation_id: str | None = None

    def __post_init__(self) -> None:
        if self.engine not in ENGINES or self.locale != "ko-KR":
            raise ValueError("unsupported GEO engine/locale")
        if not self.run_id or not self.query_id or not self.mode:
            raise ValueError("GEO observation identity/mode is incomplete")
        moment = datetime.fromisoformat(self.queried_at)
        if moment.tzinfo is None or moment.utcoffset() != timedelta(0):
            raise ValueError("queried_at must be timezone-aware UTC")
        if not self.region or not self.model or any("\n" in value for value in (self.region, self.model, self.mode)):
            raise ValueError("region/model/mode need explicit single-line values or unknown")
        if re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", self.region) or ":" in self.region:
            raise ValueError("record a region label, never a raw IP address")
        if type(self.mentioned) is not bool or type(self.recommended) is not bool:
            raise ValueError("GEO mentioned/recommended must be explicit booleans")
        if not re.fullmatch(r"[a-f0-9]{64}", self.evidence_sha256):
            raise ValueError("GEO raw evidence SHA-256 is required")
        if self.recommended and not self.recommendation_excerpt.strip():
            raise ValueError("a citation is not a recommendation; supply explicit recommendation evidence")
        for url in self.cited_urls:
            _public_url(url)


def validate_geo_evidence(observation: GeoObservation, query: GeoQuery) -> None:
    if observation.query_id != query.query_id or observation.locale != query.locale:
        raise ValueError("GEO observation does not match its query")
    path = Path(observation.evidence_path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("raw evidence must be an existing absolute local path")
    raw = path.read_bytes()
    if not raw.strip() or hashlib.sha256(raw).hexdigest() != observation.evidence_sha256:
        raise ValueError("GEO raw evidence is empty or hash changed")
    text = raw.decode("utf-8")
    # Bind the exact query, but verify citations only inside the engine's response.
    # Query-panel target URLs or capture metadata are not citations.
    try:
        capture = json.loads(text)
    except ValueError as exc:
        raise ValueError("raw GEO capture needs structured JSON with separate query and response fields") from exc
    else:
        if not isinstance(capture, dict) or capture.get("query") != query.query:
            raise ValueError("raw GEO capture does not contain the exact registered query")
        response = capture.get("response")
        if not isinstance(response, str) or not response.strip():
            raise ValueError("JSON GEO capture needs a nonempty response string")
    for url in observation.cited_urls:
        if url not in response and unquote(url) not in unquote(response):
            raise ValueError("a cited URL is absent from the raw evidence")
    if observation.recommended and (observation.recommendation_excerpt not in response
                                    or query.target_url not in observation.recommendation_excerpt):
        raise ValueError("target recommendation excerpt is absent or does not identify the target URL")


def import_geo_observations(path: str | Path, queries: list[GeoQuery]) -> list[GeoObservation]:
    by_id = {query.query_id: query for query in queries}
    observations = []
    seen = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        raw["cited_urls"] = tuple(raw["cited_urls"])
        observation = GeoObservation(**raw)
        identity = (observation.run_id, observation.query_id, observation.engine)
        if identity in seen:
            raise ValueError("duplicate run/query/engine observation")
        seen.add(identity)
        if observation.query_id not in by_id:
            raise ValueError("GEO observation uses a phantom/unregistered query")
        validate_geo_evidence(observation, by_id[observation.query_id])
        observations.append(observation)
    return observations


def summarize_geo_panel(queries: list[GeoQuery], observations: list[GeoObservation], run_id: str) -> dict:
    by_id = {query.query_id: query for query in queries}
    cells = {}
    for observation in observations:
        if observation.run_id != run_id:
            continue
        query = by_id.get(observation.query_id)
        if query is None:
            raise ValueError("unknown GEO query")
        identity = (observation.query_id, observation.engine)
        if identity in cells:
            raise ValueError("duplicate query/engine cell")
        validate_geo_evidence(observation, query)
        cells[identity] = observation
    matrix = []
    for query in queries:
        for engine in ENGINES:
            observation = cells.get((query.query_id, engine))
            cited = None
            if observation:
                target = urlsplit(query.target_url)
                cited = any(urlsplit(url)._replace(query="", fragment="") == target._replace(query="", fragment="")
                            or unquote(urlsplit(url).path) == unquote(target.path) and urlsplit(url).netloc == target.netloc
                            for url in observation.cited_urls)
            matrix.append({"query_id": query.query_id, "engine": engine,
                           "status": "OBSERVED" if observation else "UNOBSERVED",
                           "target_cited": cited,
                           "recommended": observation.recommended if observation else None,
                           "mentioned": observation.mentioned if observation else None})
    observed = [cell for cell in matrix if cell["status"] == "OBSERVED"]
    modes = Counter((observation.engine, observation.mode, observation.region, observation.model)
                    for observation in cells.values())
    return {"schema": "GeoPanelSummary/v1", "run_id": run_id, "expected_cells": len(matrix),
            "observed_cells": len(observed), "unobserved_cells": len(matrix) - len(observed),
            "target_citations": sum(cell["target_cited"] is True for cell in observed),
            "recommendations": sum(cell["recommended"] is True for cell in observed),
            "citation_rate_observed_only": (sum(cell["target_cited"] is True for cell in observed) / len(observed) if observed else None),
            "matrix": matrix,
            "observation_contexts": [{"engine": context[0], "mode": context[1], "region": context[2],
                                      "model": context[3], "observations": count} for context, count in sorted(modes.items())],
            "scope": "raw observations; engine modes kept, no causal or ranking inference"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    panel = commands.add_parser("panel")
    panel.add_argument("--registry", required=True)
    panel.add_argument("--site", required=True)
    panel.add_argument("--base-url", required=True)
    panel.add_argument("--dataset")
    panel.add_argument("--count", type=int, default=20)
    panel.add_argument("--out", required=True)
    observe = commands.add_parser("import")
    observe.add_argument("--panel", required=True)
    observe.add_argument("--observations", required=True)
    observe.add_argument("--run-id", required=True)
    observe.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "panel":
        dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8")) if args.dataset else None
        queries = build_geo_queries(read_intent_registry(args.registry), args.site, args.base_url, count=args.count, dataset=dataset)
        write_query_panel(args.out, queries)
        print(json.dumps({"status": "PANEL_READY", "queries": len(queries), "expected_cells": len(queries) * len(ENGINES), "output": args.out}))
        return 0
    queries = read_query_panel(args.panel)
    observations = import_geo_observations(args.observations, queries)
    result = summarize_geo_panel(queries, observations, args.run_id)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items() if key != "matrix"}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
