import csv

import pytest

from blog.acquisition_probe import (
    AcquisitionObservation,
    assess_gsc_readiness,
    campaign_url,
    import_gsc_csv,
    read_observations,
    write_observations,
)
from blog.search_intent import (
    SearchIntent,
    build_intent_registry,
    canonical_url,
    read_intent_registry,
    validate_registry,
    write_intent_registry,
)


BASE_URL = "https://example.test/realestate"


def _row(gu, name, *, n=40):
    return {
        "gu": gu, "name": name, "product_type": "아파트", "area_m2": 59.0,
        "molit_n": n, "molit_recent_eok": 9.0,
    }


def _registry():
    ds = {"complexes": [
        _row("노원", "상계주공2단지"),
        _row("노원", "표본미달", n=29),
        _row("강남", "강남테스트"),
    ]}
    return build_intent_registry(
        ds, "2026-09-26", daily_title="오늘 제목", daily_description="오늘 설명",
    )


def test_registry_has_one_owner_per_query_family_and_only_gated_complexes(tmp_path):
    intents = _registry()
    assert [intent.entity_type for intent in intents].count("daily") == 1
    assert [intent.entity_type for intent in intents].count("district") == 2
    assert [intent.entity_type for intent in intents].count("complex") == 2
    assert all("표본미달" not in intent.target_query for intent in intents)
    validate_registry(intents)
    path = write_intent_registry(tmp_path / "search-intents.jsonl", intents)
    assert read_intent_registry(path) == intents


def test_duplicate_query_family_is_rejected():
    original = _registry()[0]
    duplicate = SearchIntent(
        **{**original.as_dict(), "intent_id": "daily:other", "canonical_path": "/daily/other.html"}
    )
    with pytest.raises(ValueError, match="duplicate query_family"):
        validate_registry([original, duplicate])


def test_campaign_url_is_stable_and_preserves_unrelated_query():
    url = campaign_url(
        "https://example.test/explorer.html?gu=노원#results", source="tistory",
        medium="referral", campaign_id="realestate-daily", content_id="daily:2026-09-26",
    )
    assert "gu=%EB%85%B8%EC%9B%90" in url
    assert "utm_source=tistory" in url
    assert "utm_campaign=realestate-daily" in url
    assert "utm_content=daily%3A2026-09-26" in url
    assert url.endswith("#results")


def test_gsc_csv_import_maps_canonical_owner_filters_brand_and_keeps_raw_counts(tmp_path):
    intents = _registry()
    complex_intent = next(intent for intent in intents if intent.entity_type == "complex")
    csv_path = tmp_path / "gsc.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Date", "Page", "Query", "Clicks", "Impressions", "Position"])
        writer.writeheader()
        writer.writerow({
            "Date": "2026-09-01", "Page": canonical_url(BASE_URL, complex_intent),
            "Query": "상계주공 실거래", "Clicks": "2", "Impressions": "30", "Position": "5.0",
        })
        writer.writerow({
            "Date": "2026-09-01", "Page": canonical_url(BASE_URL, complex_intent),
            "Query": "hexisteme 상계주공", "Clicks": "1", "Impressions": "10", "Position": "2.0",
        })
        writer.writerow({
            "Date": "2026-09-01", "Page": "https://example.test/unowned.html",
            "Query": "다른 페이지", "Clicks": "9", "Impressions": "99", "Position": "1.0",
        })
    rows = import_gsc_csv(csv_path, intents, BASE_URL, brand_terms=("hexisteme",))
    assert len(rows) == 1
    assert rows[0].intent_id == complex_intent.intent_id
    assert rows[0].impressions == 30 and rows[0].clicks == 2
    assert rows[0].average_position == 5.0 and rows[0].indexed is True


def test_readiness_never_turns_insufficient_baseline_into_ready(tmp_path):
    intents = _registry()
    observations = [
        AcquisitionObservation(
            observed_date="2026-09-01", intent_id=intent.intent_id,
            canonical_path=intent.canonical_path, source="gsc",
            impressions=20, clicks=1, average_position=8.0, indexed=True,
        )
        for intent in intents
    ]
    blocked = assess_gsc_readiness(observations, required_urls=16)
    assert blocked.status == "NOT_OPENED_INSUFFICIENT_BASELINE"
    assert blocked.eligible_urls == len(intents)
    ready = assess_gsc_readiness(observations, required_urls=len(intents))
    assert ready.status == "READY"
    path = write_observations(tmp_path / "observations.jsonl", observations)
    assert read_observations(path) == observations
