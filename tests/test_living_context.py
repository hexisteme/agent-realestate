import copy
import json
from datetime import date

from blog.living_context import attach_living_context


def _dataset(*rows):
    return {"generated": "2026-09-22", "complexes": list(rows)}


def _row(name="테스트단지", gu="노원", **extra):
    row = {
        "name": name,
        "gu": gu,
        "score": 4.25,
        "rank": 1,
        "review_score": 4.9,
        "nearest_elem_school": "가까운초",
        "academy_exam": 17,
        "school_achievement": 82.5,
        "tukmokgo_pct": 3.2,
        "slope_pct": 4.2,
    }
    row.update(extra)
    return row


def _write_reviews(tmp_path, payload):
    path = tmp_path / "reviews.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_normalizes_legacy_review_but_global_meta_date_does_not_make_it_current(tmp_path):
    reviews = _write_reviews(tmp_path, {
        "_meta": {"confirmed_date": "2026-09-21", "bias_warning": "표본 편향 주의"},
        "테스트단지": {
            "source": "legacy-community",
            "n_seen": 7,
            "themes_pos": ["조용함"],
            "themes_caution": ["주차"],
            "notable": ["원문은 노출하면 안 됨"],
        },
    })

    out = attach_living_context(_dataset(_row()), reviews, "2026-09-22")
    context = out["complexes"][0]["living_context"]

    assert context["reviews"] == {
        "status": "legacy_unverified",
        "stale": False,
        "n_seen": 7,
        "themes_pos": ["조용함"],
        "themes_caution": ["주차"],
        "source_label": "legacy-community",
        "source_url": None,
        "observed_date": None,
        "bias_warning": (
            "커뮤니티 후기는 작성자 자기선택·소표본·시점 편향이 있으며, "
            "단지 전체의 대표 평가나 점수·순위 근거가 아닙니다."
        ),
        "warning": "항목별 관측일이 없는 기존 후기 표본이며 현재 상태로 간주하지 않습니다.",
    }
    assert "review_score" not in context["reviews"]
    assert "notable" not in context["reviews"]


def test_normalizes_daangn_shape_and_applies_review_staleness(tmp_path):
    reviews = _write_reviews(tmp_path, {
        "[동작구]신동아리버파크": {
            "source": "당근부동산",
            "url": "https://realty.daangn.com/complexes/1",
            "n": 3,
            "themes": {"pos": ["공원"], "caution": ["소음"]},
            "confirmed": "2026-08-01",
            "notable_quotes": ["원문 인용"],
            "rating": 5,
        }
    })

    out = attach_living_context(
        _dataset(_row("신동아리버파크", "동작")), reviews, date(2026, 9, 22)
    )
    normalized = out["complexes"][0]["living_context"]["reviews"]

    assert normalized["status"] == "stale"
    assert normalized["stale"] is True
    assert normalized["observed_date"] == "2026-08-01"
    assert normalized["n_seen"] == 3
    assert normalized["themes_pos"] == ["공원"]
    assert normalized["themes_caution"] == ["소음"]
    assert normalized["source_url"] == "https://realty.daangn.com/complexes/1"
    assert "notable_quotes" not in normalized
    assert "rating" not in normalized


def test_plain_name_match_is_rejected_when_dataset_name_is_ambiguous(tmp_path):
    reviews = _write_reviews(tmp_path, {
        "현대": {"n_seen": 9, "themes_pos": ["역세권"], "confirmed_date": "2026-09-20"},
        "[강남구]현대": {"n_seen": 2, "themes_caution": ["주차"], "confirmed_date": "2026-09-20"},
    })
    ds = _dataset(_row("현대", "강남"), _row("현대", "서초", rank=2))

    out = attach_living_context(ds, reviews, "2026-09-22")
    first, second = out["complexes"]

    assert first["living_context"]["reviews"]["n_seen"] == 2
    assert first["living_context"]["reviews"]["status"] == "current"
    assert second["living_context"]["reviews"]["status"] == "missing"
    assert second["living_context"]["reviews"]["n_seen"] == 0


def test_evidence_dates_make_school_current_or_stale_and_labels_are_non_assignment(tmp_path):
    reviews = _write_reviews(tmp_path, {})
    ds = _dataset(_row("A", "노원"), _row("B", "도봉", rank=2))
    evidence = {
        "school": {
            "[노원구]A": {
                "observed_date": "2026-09-01",
                "source_label": "Kakao Local SC4",
                "source_url": "https://developers.kakao.com/docs/latest/ko/local/dev-guide",
            },
            "[도봉구]B": {"observed_date": "2026-06-01", "source_label": "Kakao Local SC4"},
        },
        "terrain": {
            "[노원구]A": {"observed_date": "2026-09-01", "source_label": "OpenTopoData SRTM30m"},
        },
    }

    out = attach_living_context(ds, reviews, "2026-09-22", evidence)
    school_a = out["complexes"][0]["living_context"]["school"]
    school_b = out["complexes"][1]["living_context"]["school"]
    terrain_a = out["complexes"][0]["living_context"]["terrain"]
    terrain_b = out["complexes"][1]["living_context"]["terrain"]

    assert school_a["status"] == "current"
    assert school_b["status"] == "stale"
    assert school_b["stale"] is True
    assert "최근접" in school_a["scope_label"]
    assert "배정학교 아님" in school_a["scope_label"]
    assert school_a["school_achievement"] == 82.5
    assert school_a["tukmokgo_pct"] == 3.2
    assert terrain_a["status"] == "current"
    assert terrain_a["method_label"] == "SRTM30m 중심±150m 표고 기반 경사 근사(보행 경사 아님)"
    assert terrain_b["status"] == "legacy_unverified"
    assert terrain_b["slope_pct"] == 4.2


def test_missing_values_have_explicit_entries_and_scores_order_are_unchanged(tmp_path):
    reviews = _write_reviews(tmp_path, {})
    ds = _dataset(
        _row("A", nearest_elem_school=None, academy_exam=None, school_achievement=None,
             tukmokgo_pct=None, slope_pct=None),
        _row("B", rank=2, score=3.75),
    )
    before = copy.deepcopy(ds)

    out = attach_living_context(ds, reviews, "2026-09-22")

    assert ds == before
    assert [row["name"] for row in out["complexes"]] == ["A", "B"]
    assert [(row["score"], row["rank"], row["review_score"]) for row in out["complexes"]] == [
        (4.25, 1, 4.9), (3.75, 2, 4.9)
    ]
    empty = out["complexes"][0]["living_context"]
    assert empty["school"]["status"] == "missing"
    assert empty["terrain"]["status"] == "missing"
    assert empty["reviews"]["status"] == "missing"


def test_hostile_strings_remain_plain_data_and_raw_bodies_are_dropped(tmp_path):
    hostile = '<img src=x onerror="raise()"><script>boom()</script>'
    reviews = _write_reviews(tmp_path, {
        "테스트단지": {
            "source": hostile,
            "n_seen": 1,
            "themes_pos": [hostile],
            "themes_caution": [],
            "confirmed_date": "2026-09-22",
            "notable_quotes": [hostile],
            "raw_body": hostile,
        }
    })

    out = attach_living_context(_dataset(_row()), reviews, "2026-09-22")
    normalized = out["complexes"][0]["living_context"]["reviews"]

    assert normalized["source_label"] == hostile
    assert normalized["themes_pos"] == [hostile]
    assert set(normalized) == {
        "status", "stale", "n_seen", "themes_pos", "themes_caution",
        "source_label", "source_url", "observed_date", "bias_warning", "warning",
    }


def test_review_themes_neutralize_public_wording_gate_terms(tmp_path):
    reviews = _write_reviews(tmp_path, {
        "테스트단지": {
            "n_seen": 2,
            "themes_pos": ["급등 기대 추천", "급등 기대 추천"],
            "themes_caution": ["호가 고평가 우려"],
            "confirmed_date": "2026-09-22",
        }
    })

    out = attach_living_context(_dataset(_row()), reviews, "2026-09-22")
    normalized = out["complexes"][0]["living_context"]["reviews"]

    assert normalized["themes_pos"] == ["단기 가격 변동 기대 선호 의견"]
    assert normalized["themes_caution"] == ["표시 가격 가격 평가 우려"]


def test_legacy_review_source_keys_use_first_url_and_internal_bias_note_is_not_public(tmp_path):
    reviews = _write_reviews(tmp_path, {
        "_meta": {"bias_warning": "내부 수집 메모 " * 30},
        "테스트단지": {
            "n_seen": 2,
            "_source": "집계 출처",
            "_url": "https://example.com/one ; https://example.com/two",
        },
    })

    out = attach_living_context(_dataset(_row()), reviews, "2026-09-22")
    normalized = out["complexes"][0]["living_context"]["reviews"]

    assert normalized["source_label"] == "집계 출처"
    assert normalized["source_url"] == "https://example.com/one"
    assert normalized["bias_warning"] == (
        "커뮤니티 후기는 작성자 자기선택·소표본·시점 편향이 있으며, "
        "단지 전체의 대표 평가나 점수·순위 근거가 아닙니다."
    )


def test_staleness_thresholds_are_strictly_greater_than_30_and_90_days(tmp_path):
    reviews = _write_reviews(tmp_path, {
        "테스트단지": {"n_seen": 1, "confirmed_date": "2026-08-23"},
    })
    evidence = {
        "school": {"테스트단지": {"observed_date": "2026-06-24"}},
    }

    out = attach_living_context(_dataset(_row()), reviews, "2026-09-22", evidence)
    context = out["complexes"][0]["living_context"]

    assert context["reviews"]["status"] == "current"  # 정확히 30일
    assert context["school"]["status"] == "current"  # 정확히 90일
