import csv
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from blog.acquisition_probe import (
    AcquisitionObservation, _window_totals, build_treatment_overlay, check_probe_guardrail,
    evaluate_acquisition_probe, freeze_probe_cohort, import_gsc_csv,
    prepare_acquisition_probe, restore_probe_cohort, validate_probe_cohort, write_probe,
)
from blog.brand_identity import AUTHOR_LABEL
from blog.geo_visibility import (
    GeoObservation, build_geo_queries, import_geo_observations,
    summarize_geo_panel, validate_geo_evidence,
)
from blog.search_intent import build_intent_registry, canonical_url
from blog.seo_geo_audit import audit_site


BASE = "https://example.test/realestate"
D0 = "2026-11-01"


def _dataset():
    return {"complexes": [{"gu": "노원" if index % 2 else "강남", "name": f"검증단지{index:02d}",
                           "product_type": "아파트", "area_m2": 59.0, "molit_n": 40,
                           "molit_recent_eok": 9.0} for index in range(20)]}


def _intents():
    return build_intent_registry(_dataset(), D0, daily_title="서울 아파트 오늘 실거래", daily_description="서울 오늘의 공공 거래")


def _gsc_export(tmp_path, intents, start, end, *, treatment_ids=(), missing=None):
    raw = tmp_path / f"gsc-{start}.csv"
    with raw.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Date", "Page", "Query", "Clicks", "Impressions", "Position"])
        writer.writeheader()
        for intent in intents:
            if intent.entity_type != "complex" or intent.intent_id == missing:
                continue
            writer.writerow({"Date": end, "Page": canonical_url(BASE, intent), "Query": intent.target_query,
                             "Clicks": "1", "Impressions": "100" if intent.intent_id in treatment_ids else "30", "Position": "5"})
    return import_gsc_csv(raw, intents, BASE, brand_terms=("hexisteme", "데이터랩"), coverage_start=start, coverage_end=end)


def _probe(tmp_path):
    intents = _intents()
    observations = _gsc_export(tmp_path, intents, "2026-10-04", "2026-10-31")
    probe = prepare_acquisition_probe(intents, observations, _dataset(), probe_id="realestate-first",
                                      d0=D0, deployment_date="2026-09-26", deployment_id="deployment-final",
                                      seed=20260926)
    return probe, intents, observations


def _guardrails(probe):
    return [check_probe_guardrail(probe, checked_date=day, audit={"schema": "SeoGeoAudit/v1", "status": "PASS"},
                                   latest_deployment_date=probe["deployment_date"], latest_deployment_id=probe["deployment_id"],
                                   facts_valid=True, publisher_valid=True, wording_valid=True,
                                   cohort_validation={"schema": "AcquisitionCohortCheck/v1", "probe_id": probe["probe_id"], "status": "PASS"})
            for day in [*probe["guardrail_dates"], probe["evaluation_date"]]]


def _write_page(site, intent, *, marker="", schema=True, hidden_creator=False):
    filename = site / unquote(intent.canonical_path).lstrip("/")
    filename.parent.mkdir(parents=True, exist_ok=True)
    creator = "" if hidden_creator else AUTHOR_LABEL
    dataset = {"@context": "https://schema.org", "@type": "Dataset", "name": " ".join(intent.entity_ids),
               "description": "실거래", "dateModified": intent.observed_at,
               "creator": {"@type": "Organization", "name": AUTHOR_LABEL}}
    body = f'''<!doctype html><html lang="ko"><head><title>{intent.title}</title>
    <meta name="description" content="{intent.description}"><link rel="canonical" href="{canonical_url(BASE, intent)}">
    {f'<script type="application/ld+json">{json.dumps(dataset, ensure_ascii=False)}</script>' if schema else ''}</head>
    <body><main><article><h1>{intent.target_query}</h1><p>{creator} {intent.observed_at}</p>
    {marker}<a href="../index.html">홈</a></article></main></body></html>'''
    filename.write_text(body, encoding="utf-8")
    return filename


def _site(tmp_path, intents):
    site = tmp_path / "site"
    site.mkdir()
    (site / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (site / "index.html").write_text('<html><body id="root">홈</body></html>', encoding="utf-8")
    for intent in intents:
        _write_page(site, intent)
    return site


def test_preregistration_is_seeded_unique_and_has_exact_inclusive_windows(tmp_path):
    probe, intents, observations = _probe(tmp_path)
    assert probe["status"] == "PREREGISTERED"
    assert len(probe["pairs"]) == 8 and probe["eligible_urls"] == 20
    assert len({p[arm]["intent_id"] for p in probe["pairs"] for arm in ("treatment", "control")}) == 16
    assert probe["baseline_start"] == "2026-10-04" and probe["baseline_end"] == "2026-10-31"
    assert probe["post_start"] == "2026-11-01" and probe["post_end"] == "2026-11-28"
    assert probe["evaluation_date"] == "2026-11-29"
    assert prepare_acquisition_probe(intents, observations, _dataset(), probe_id="realestate-first", d0=D0,
                                     deployment_date="2026-09-26", deployment_id="deployment-final", seed=20260926) == probe
    output = tmp_path / "probe.json"
    write_probe(output, probe)
    with pytest.raises(FileExistsError):
        write_probe(output, {"status": "WON"})
    assert json.loads(output.read_text())["status"] == "PREREGISTERED"


def test_fresh_deployment_and_missing_baseline_do_not_open_probe(tmp_path):
    probe = prepare_acquisition_probe(_intents(), [], _dataset(), probe_id="new", d0="2026-09-27",
                                      deployment_date="2026-09-26", deployment_id="deploy", seed=1)
    assert probe["status"] == "NOT_OPENED_INSUFFICIENT_BASELINE" and not probe["pairs"]
    probe = prepare_acquisition_probe(_intents(), [], _dataset(), probe_id="new", d0=D0,
                                      deployment_date="2026-09-26", deployment_id="deploy", seed=1)
    assert probe["eligible_urls"] == 0


def test_probe_rejects_uncertified_or_changed_gsc_and_duplicate_imports(tmp_path):
    probe, intents, observations = _probe(tmp_path)
    kwargs = dict(probe_id="new", d0=D0, deployment_date="2026-09-26", deployment_id="deploy", seed=1)
    with pytest.raises(ValueError, match="nonbrand"):
        prepare_acquisition_probe(intents, [replace(row, nonbrand=None) for row in observations], _dataset(), **kwargs)
    with pytest.raises(ValueError, match="duplicate"):
        prepare_acquisition_probe(intents, observations + observations, _dataset(), **kwargs)
    with pytest.raises(ValueError, match="do not match"):
        prepare_acquisition_probe(intents, [replace(row, impressions=row.impressions + 100) for row in observations], _dataset(), **kwargs)
    Path(observations[0].provenance_path).write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        prepare_acquisition_probe(intents, observations, _dataset(), **kwargs)


def test_d28_evaluation_preserves_missingness_and_never_judges_early(tmp_path):
    probe, intents, _ = _probe(tmp_path)
    treatments = set(build_treatment_overlay(probe))
    post = _gsc_export(tmp_path, intents, probe["post_start"], probe["post_end"], treatment_ids=treatments)
    assert evaluate_acquisition_probe(probe, post, evaluated_date="2026-11-28", guardrails=_guardrails(probe))["status"] == "NOT_DUE"
    result = evaluate_acquisition_probe(probe, post, evaluated_date="2026-11-29", guardrails=_guardrails(probe))
    assert result["status"] == "WON" and result["treatment_won_pairs"] == 8
    assert result["statistical_significance_claimed"] is False
    result = evaluate_acquisition_probe(probe, post, evaluated_date="2026-11-29", guardrails=[])
    assert result["status"] == "INCONCLUSIVE"
    missing = post[0].intent_id
    missing_post = _gsc_export(tmp_path, intents, probe["post_start"], probe["post_end"],
                               treatment_ids=treatments, missing=missing)
    result = evaluate_acquisition_probe(probe, missing_post, evaluated_date="2026-11-29", guardrails=_guardrails(probe))
    if missing in {p[arm]["intent_id"] for p in probe["pairs"] for arm in ("treatment", "control")}:
        assert result["status"] == "INCONCLUSIVE" and result["completed_pairs"] == 7


def test_gsc_strict_totals_reject_a_missing_raw_date(tmp_path):
    intent = next(row for row in _intents() if row.entity_type == "complex")
    raw = tmp_path / "two-days.csv"
    raw.write_text("Date,Page,Query,Clicks,Impressions,Position\n"
                   f"2026-10-04,{canonical_url(BASE, intent)},아파트 실거래,1,30,5\n"
                   f"2026-10-31,{canonical_url(BASE, intent)},아파트 실거래,1,300,5\n", encoding="utf-8")
    rows = import_gsc_csv(raw, [intent], BASE, brand_terms=("데이터랩",),
                          coverage_start="2026-10-04", coverage_end="2026-10-31")
    assert _window_totals(rows, "2026-10-04", "2026-10-31")[intent.intent_id]["impressions"] == 330
    with pytest.raises(ValueError, match="incomplete|missing"):
        _window_totals(rows[:1], "2026-10-04", "2026-10-31")


def test_gsc_strict_totals_reject_a_wholly_missing_imported_owner(tmp_path):
    rows = _gsc_export(tmp_path, _intents(), "2026-10-04", "2026-10-31")
    missing = rows[0].intent_id
    with pytest.raises(ValueError, match="incomplete|missing"):
        _window_totals([row for row in rows if row.intent_id != missing], "2026-10-04", "2026-10-31")


def test_gsc_strict_totals_require_recorded_import_scope(tmp_path):
    rows = _gsc_export(tmp_path, _intents(), "2026-10-04", "2026-10-31")
    legacy = []
    for row in rows:
        values = row.as_dict()
        values.pop("import_scope_paths", None)
        legacy.append(AcquisitionObservation(**values))
    with pytest.raises(ValueError, match="scope"):
        _window_totals(legacy, "2026-10-04", "2026-10-31")


def test_gsc_strict_totals_reject_raw_dates_outside_declared_window(tmp_path):
    intent = next(row for row in _intents() if row.entity_type == "complex")
    raw = tmp_path / "wrong-window.csv"
    raw.write_text("Date,Page,Query,Clicks,Impressions,Position\n"
                   f"2026-10-03,{canonical_url(BASE, intent)},아파트 실거래,1,300,5\n"
                   f"2026-10-31,{canonical_url(BASE, intent)},아파트 실거래,1,30,5\n", encoding="utf-8")
    rows = import_gsc_csv(raw, [intent], BASE, brand_terms=("데이터랩",))
    in_window = [replace(row, coverage_start="2026-10-04", coverage_end="2026-10-31")
                 for row in rows if row.observed_date == "2026-10-31"]
    with pytest.raises(ValueError, match="outside|window"):
        _window_totals(in_window, "2026-10-04", "2026-10-31")


def test_gsc_complete_import_excludes_unowned_and_brand_rows(tmp_path):
    intent = next(row for row in _intents() if row.entity_type == "complex")
    raw = tmp_path / "scoped.csv"
    raw.write_text("Date,Page,Query,Clicks,Impressions,Position\n"
                   f"2026-10-04,{canonical_url(BASE, intent)},아파트 실거래,1,30,5\n"
                   f"2026-10-31,{canonical_url(BASE, intent)},아파트 실거래,1,300,5\n"
                   f"2026-10-31,{canonical_url(BASE, intent)},데이터랩,1,900,5\n"
                   f"2026-10-31,{BASE}/unowned.html,아파트 실거래,1,800,5\n", encoding="utf-8")
    rows = import_gsc_csv(raw, [intent], BASE, brand_terms=("데이터랩",),
                          coverage_start="2026-10-04", coverage_end="2026-10-31")
    totals = _window_totals(rows, "2026-10-04", "2026-10-31")
    assert list(totals) == [intent.intent_id]
    assert totals[intent.intent_id]["impressions"] == 330


def test_guardrail_stops_same_day_unregistered_deployment_and_missing_hash_evidence(tmp_path):
    probe, _, _ = _probe(tmp_path)
    args = dict(checked_date=probe["guardrail_dates"][0], audit={"schema": "SeoGeoAudit/v1", "status": "PASS"},
                latest_deployment_date=probe["deployment_date"], latest_deployment_id="changed-on-same-day",
                facts_valid=True, publisher_valid=True, wording_valid=True)
    assert check_probe_guardrail(probe, **args)["status"] == "STOP"


def test_frozen_payloads_and_registry_survive_daily_rebuild_and_tampering_stops(tmp_path):
    probe, intents, _ = _probe(tmp_path)
    site = _site(tmp_path, intents)
    treatments = build_treatment_overlay(probe)
    for intent in intents:
        if intent.intent_id in treatments:
            _write_page(site, intent, marker=f'<section data-acquisition-probe="{probe["probe_id"]}">실거래 직답</section>')
    cohort_path = tmp_path / "cohort.json"
    cohort = freeze_probe_cohort(probe, intents, site, BASE, cohort_path, frozen_date=D0)
    assert validate_probe_cohort(cohort, site, BASE)["status"] == "PASS"
    frozen_id = cohort["owners"][0]["intent"]["intent_id"]
    changed_intents = [replace(intent, title="새 날의 제목", observed_at="2026-11-02") if intent.intent_id == frozen_id else intent for intent in intents]
    changed = next(intent for intent in changed_intents if intent.intent_id == frozen_id)
    _write_page(site, changed)
    assert validate_probe_cohort(cohort, site, BASE)["status"] == "FAIL"
    restored = restore_probe_cohort(cohort_path, site, BASE, changed_intents, today="2026-11-02")
    assert restored == intents and validate_probe_cohort(cohort, site, BASE)["status"] == "PASS"
    Path(cohort["owners"][0]["snapshot_path"]).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="snapshot changed"):
        restore_probe_cohort(cohort_path, site, BASE, changed_intents, today="2026-11-02")


def test_deterministic_audit_flags_actionable_regressions_not_html_dump(tmp_path):
    intents = _intents()
    site = _site(tmp_path, intents)
    assert audit_site(site, intents, BASE)["status"] == "PASS"
    _write_page(site, intents[-1], hidden_creator=True)
    path = site / unquote(intents[-1].canonical_path).lstrip("/")
    path.write_text(path.read_text().replace('</article>', '<a href="../missing.html">깨진 링크</a></article>'), encoding="utf-8")
    result = audit_site(site, intents, BASE)
    assert result["counts_by_code"] == {"BROKEN_INTERNAL_LINK": 1, "SCHEMA_CREATOR_HIDDEN": 1}
    assert all(finding["action"] and "<!doctype" not in finding["detail"] for finding in result["findings"])


def test_audit_rejects_locale_schema_canonical_and_indexability_mismatch(tmp_path):
    intents = _intents()[:1]
    site = _site(tmp_path, intents)
    path = site / unquote(intents[0].canonical_path).lstrip("/")
    path.write_text(path.read_text().replace('lang="ko"', 'lang="en"').replace('</head>', '<meta name="robots" content="noindex"></head>'), encoding="utf-8")
    result = audit_site(site, intents, BASE)
    assert set(result["counts_by_code"]) == {"LOCALE_MISMATCH", "NOINDEX"}


def test_svg_accessible_title_is_not_the_document_head_title(tmp_path):
    intents = _intents()[:1]
    site = _site(tmp_path, intents)
    path = site / unquote(intents[0].canonical_path).lstrip("/")
    path.write_text(path.read_text().replace('</article>', '<svg><circle><title>월별 거래 9억</title></circle></svg></article>'), encoding="utf-8")
    assert audit_site(site, intents, BASE)["status"] == "PASS"


def test_actual_peer_fallback_fragment_matches_rendered_gu_dom_id():
    from blog.complex_page import render_complex_page
    from blog.gu_hub import render_gu_hub
    from blog.seo_geo_audit import ParsedPage

    row = {"gu": "노원", "name": "감시주공", "product_type": "아파트", "area_m2": 59.0, "molit_n": 40,
           "molit_recent_eok": 9.0}
    peer = {**row, "name": "한글 이웃단지", "molit_n": 20}
    gu = ParsedPage(render_gu_hub("노원", [row, peer], D0, D0))
    page = ParsedPage(render_complex_page(row, [peer], None, D0, D0))
    fallbacks = [urlsplit(link).fragment for link in page.links if "gu/" in link and urlsplit(link).fragment]
    assert len(fallbacks) == 1
    assert unquote(fallbacks[0]) in gu.ids


def test_geo_panel_uses_twenty_real_korean_owners_and_missing_engines_are_unknown(tmp_path):
    intents = _intents()
    site = _site(tmp_path, intents)
    panel = build_geo_queries(intents, site, BASE, dataset=_dataset())
    assert len(panel) == 20 and len({query.target_url for query in panel}) == 20
    assert all(query.locale == "ko-KR" for query in panel)
    query = panel[0]
    evidence = tmp_path / "raw-capture.json"
    evidence.write_text(json.dumps({"query": query.query, "response": f"공공데이터 출처: {query.target_url}"}, ensure_ascii=False), encoding="utf-8")
    observation = GeoObservation("first", query.query_id, "perplexity", "browser_search", "2026-09-26T12:00:00+00:00",
                                 "ko-KR", str(evidence), hashlib.sha256(evidence.read_bytes()).hexdigest(),
                                 (query.target_url,), mentioned=True, recommended=False, region="KR", model="unknown")
    result = summarize_geo_panel(panel, [observation], "first")
    assert result["expected_cells"] == 60 and result["observed_cells"] == 1 and result["unobserved_cells"] == 59
    assert result["target_citations"] == 1 and result["recommendations"] == 0
    assert all(cell["target_cited"] is None for cell in result["matrix"] if cell["status"] == "UNOBSERVED")
    assert summarize_geo_panel(panel, [], "empty")["citation_rate_observed_only"] is None


def test_geo_provenance_rejects_phantoms_wrong_language_unbound_response_and_fabricated_citations(tmp_path):
    intents = _intents()
    site = _site(tmp_path, intents)
    panel = build_geo_queries(intents, site, BASE, dataset=_dataset())
    query = panel[0]
    raw = tmp_path / "capture.txt"
    raw.write_text(json.dumps({"query": query.query, "response": query.target_url}, ensure_ascii=False), encoding="utf-8")
    observation = GeoObservation("first", query.query_id, "chatgpt", "search", "2026-09-26T12:00:00Z", "ko-KR", str(raw),
                                 hashlib.sha256(raw.read_bytes()).hexdigest(), (query.target_url,), True, False)
    validate_geo_evidence(observation, query)
    with pytest.raises(ValueError, match="citation is not a recommendation"):
        replace(observation, recommended=True)
    with pytest.raises(ValueError, match="absent"):
        validate_geo_evidence(replace(observation, cited_urls=("https://example.test/invented",)), query)
    raw.write_text(json.dumps({"query": "Unbound query", "response": query.target_url}), encoding="utf-8")
    with pytest.raises(ValueError, match="exact registered query"):
        validate_geo_evidence(replace(observation, evidence_sha256=hashlib.sha256(raw.read_bytes()).hexdigest()), query)
    target = site / unquote(urlsplit(query.target_url).path[len("/realestate/"):])
    target.unlink()
    with pytest.raises(ValueError, match="phantom"):
        build_geo_queries(intents, site, BASE, dataset=_dataset())


def test_geo_jsonl_import_rejects_repeated_cells_and_unknown_query(tmp_path):
    intents = _intents()
    site = _site(tmp_path, intents)
    panel = build_geo_queries(intents, site, BASE, dataset=_dataset())
    query = panel[0]
    evidence = tmp_path / "capture.txt"
    evidence.write_text(json.dumps({"query": query.query, "response": "無引用"}, ensure_ascii=False), encoding="utf-8")
    observation = GeoObservation("first", query.query_id, "google_ai", "ai_overview", "2026-09-26T12:00:00Z", "ko-KR",
                                 str(evidence), hashlib.sha256(evidence.read_bytes()).hexdigest(), (), False, False)
    from dataclasses import asdict
    source = tmp_path / "observations.jsonl"
    source.write_text(json.dumps(asdict(observation)) + "\n" + json.dumps(asdict(observation)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        import_geo_observations(source, panel)


def test_geo_target_url_in_capture_metadata_is_not_a_response_citation(tmp_path):
    intents = _intents()
    site = _site(tmp_path, intents)
    query = build_geo_queries(intents, site, BASE, dataset=_dataset())[0]
    evidence = tmp_path / "capture.json"
    evidence.write_text(json.dumps({"query": query.query, "target_url": query.target_url,
                                   "response": "검색 결과를 찾지 못했습니다."}, ensure_ascii=False), encoding="utf-8")
    observation = GeoObservation("first", query.query_id, "chatgpt", "search", "2026-09-26T12:00:00Z", "ko-KR", str(evidence),
                                 hashlib.sha256(evidence.read_bytes()).hexdigest(), (query.target_url,), False, False)
    with pytest.raises(ValueError, match="cited URL is absent"):
        validate_geo_evidence(observation, query)


@pytest.mark.parametrize("metadata_label", ["Target URL (capture metadata)", "Source", "Recommendation"])
def test_geo_plaintext_metadata_never_becomes_a_response_citation(tmp_path, metadata_label):
    intents = _intents()
    query = build_geo_queries(intents, _site(tmp_path, intents), BASE, dataset=_dataset())[0]
    evidence = tmp_path / "metadata.txt"
    evidence.write_text(f"Query: {query.query}\n{metadata_label}: {query.target_url}\n"
                        "Response: 해당 자료를 찾지 못했습니다.\n", encoding="utf-8")
    observation = GeoObservation("first", query.query_id, "chatgpt", "search", "2026-09-26T12:00:00Z", "ko-KR",
                                 str(evidence), hashlib.sha256(evidence.read_bytes()).hexdigest(),
                                 (query.target_url,), False, False)
    with pytest.raises(ValueError, match="structured|JSON|response"):
        validate_geo_evidence(observation, query)


def test_geo_unstructured_plaintext_is_not_verified_even_without_citations(tmp_path):
    intents = _intents()
    query = build_geo_queries(intents, _site(tmp_path, intents), BASE, dataset=_dataset())[0]
    evidence = tmp_path / "legacy.txt"
    evidence.write_text(query.query + "\n자료 없음\n", encoding="utf-8")
    observation = GeoObservation("first", query.query_id, "chatgpt", "search", "2026-09-26T12:00:00Z", "ko-KR",
                                 str(evidence), hashlib.sha256(evidence.read_bytes()).hexdigest(), (), False, False)
    with pytest.raises(ValueError, match="structured|JSON|response"):
        validate_geo_evidence(observation, query)


@pytest.mark.parametrize("value", [True, -1])
def test_acquisition_count_types_are_not_booleans_or_negative(value):
    with pytest.raises(ValueError, match="counts"):
        AcquisitionObservation("2026-09-26", "complex:test", "/complex/test.html", "gsc", impressions=value)
