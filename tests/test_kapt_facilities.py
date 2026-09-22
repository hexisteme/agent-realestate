import json

import blog.build_explorer as be
import blog.kapt_facilities as kf


def _dataset():
    return {
        "complexes": [
            {"complex_no": "101", "name": "정확단지", "gu": "노원", "units": 100, "built_year": 2001,
             "heating": "", "parking_per_unit": None},
            {"complex_no": "102", "name": "미확인단지", "gu": "도봉", "units": 200, "built_year": 2002,
             "heating": "", "parking_per_unit": None},
        ],
        "sources": [],
    }


def test_refresh_and_merge_publish_only_identity_verified_facilities(tmp_path, monkeypatch):
    dataset_path = tmp_path / "dataset.json"
    cache_path = tmp_path / "kapt.json"
    dataset_path.write_text(json.dumps(_dataset(), ensure_ascii=False), encoding="utf-8")

    def resolve(name, district, units, built_year, lookup, key, basis_cache=None):
        if name != "정확단지":
            return None
        return "K101", {
            "heating": "지역난방", "corridor_type": "계단식", "builder": "건설사",
            "parking_ground": 10, "parking_underground": 110, "parking_total": 120,
            "parking_household_count": 100, "parking_per_unit": 1.2,
        }

    monkeypatch.setattr(kf, "_resolve_kapt_basis", resolve)
    stats = kf.refresh_kapt_facilities(dataset_path, cache_path, today="2026-09-22", key="secret", checkpoint_every=0)
    assert stats == {
        "changed": True, "targets": 2, "queried": 2, "verified": 1,
        "heating": 1, "parking": 1, "missing_key": False, "service_error": False,
    }

    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["_meta"]["complete"] is True
    assert cache["_meta"]["latest_observed_date"] == "2026-09-22"
    assert cache["complexes"]["102"]["status"] == "not_found_or_identity_rejected"
    assert "secret" not in cache_path.read_text(encoding="utf-8")

    ds = be.add_kapt_facilities(_dataset(), str(cache_path))
    first, second = ds["complexes"]
    assert first["heating"] == "지역난방"
    assert first["parking_per_unit"] == 1.2
    assert (first["parking_ground"], first["parking_underground"], first["parking_total"]) == (10, 110, 120)
    assert first["parking_observed_date"] == "2026-09-22"
    assert second["heating"] == ""
    assert len(ds["sources"]) == 1

    again = kf.refresh_kapt_facilities(dataset_path, cache_path, today="2026-09-23", key="secret", checkpoint_every=0)
    assert again["queried"] == 0
    assert again["changed"] is False


def test_failed_refresh_keeps_last_verified_fact_and_observation_date(tmp_path, monkeypatch):
    dataset = {"complexes": [_dataset()["complexes"][0]]}
    dataset_path = tmp_path / "dataset.json"
    cache_path = tmp_path / "kapt.json"
    dataset_path.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(
        kf,
        "_resolve_kapt_basis",
        lambda *args, **kwargs: ("K101", {"heating": "개별난방", "parking_ground": 0,
                                           "parking_underground": 100, "parking_total": 100,
                                           "parking_household_count": 100, "parking_per_unit": 1.0}),
    )
    kf.refresh_kapt_facilities(dataset_path, cache_path, today="2026-09-22", key="secret", checkpoint_every=0)
    monkeypatch.setattr(kf, "_resolve_kapt_basis", lambda *args, **kwargs: None)
    stats = kf.refresh_kapt_facilities(dataset_path, cache_path, today="2026-09-29", key="secret", checkpoint_every=0)
    entry = json.loads(cache_path.read_text(encoding="utf-8"))["complexes"]["101"]
    assert stats["verified"] == 1
    assert entry["status"] == "refresh_failed"
    assert entry["observed_date"] == "2026-09-22"
    assert entry["last_attempt_date"] == "2026-09-29"
    assert entry["parking_per_unit"] == 1.0


def test_verified_complex_code_and_public_frame_name_survive_display_name_transform(tmp_path, monkeypatch):
    dataset_path = tmp_path / "dataset.json"
    frame_path = tmp_path / "frame.json"
    seed_path = tmp_path / "overlay.json"
    cache_path = tmp_path / "kapt.json"
    dataset_path.write_text(json.dumps({"complexes": [{
        "complex_no": "900", "name": "RTMS표시명", "gu": "노원", "units": 100, "built_year": 2001,
    }]}, ensure_ascii=False), encoding="utf-8")
    frame_path.write_text(json.dumps([{
        "complexNo": "900", "name": "Kapt정확단지", "households": 100, "builtYm": "20010101",
    }], ensure_ascii=False), encoding="utf-8")
    seed_path.write_text(json.dumps({
        "900": {"kapt_verified": True, "kapt_code": "K900"},
    }), encoding="utf-8")

    monkeypatch.setattr(kf, "fetch_basis", lambda code, key: {
        "kaptName": "Kapt정확단지", "kaptAddr": "서울특별시 노원구 동일로 1",
        "units": 100, "kaptdaCnt": 100, "hoCnt": 0, "built_year": 2001,
        "heating": "지역난방", "parking_ground": 0, "parking_underground": 120,
        "parking_total": 120, "parking_household_count": 100, "parking_per_unit": 1.2,
    })
    monkeypatch.setattr(kf, "_resolve_kapt_basis", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("seed ignored")))
    stats = kf.refresh_kapt_facilities(
        dataset_path, cache_path, today="2026-09-22", key="secret", checkpoint_every=0,
        public_frame_path=frame_path, seed_paths=(seed_path,),
    )
    entry = json.loads(cache_path.read_text(encoding="utf-8"))["complexes"]["900"]
    assert stats["verified"] == 1
    assert entry["name"] == "RTMS표시명"
    assert entry["match_name"] == "Kapt정확단지"
    assert entry["kapt_code"] == "K900"


def test_api_outage_preserves_verified_seed_values_with_original_date(tmp_path, monkeypatch):
    dataset_path = tmp_path / "dataset.json"
    seed_path = tmp_path / "enrich_overlay_25gu_20260907.json"
    cache_path = tmp_path / "kapt.json"
    dataset_path.write_text(json.dumps({"complexes": [{
        "complex_no": "700", "name": "보존단지", "gu": "도봉", "units": 300, "built_year": 1999,
    }]}, ensure_ascii=False), encoding="utf-8")
    seed_path.write_text(json.dumps({"700": {
        "kapt_verified": True, "kapt_code": "K700", "heating": "개별난방",
        "parking_per_unit": 0.77,
    }}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(kf, "fetch_basis", lambda *args, **kwargs: None)
    monkeypatch.setattr(kf, "_resolve_kapt_basis", lambda *args, **kwargs: None)

    stats = kf.refresh_kapt_facilities(
        dataset_path, cache_path, today="2026-09-22", key="secret", checkpoint_every=0,
        seed_paths=(seed_path,), seed_only=True,
    )
    entry = json.loads(cache_path.read_text(encoding="utf-8"))["complexes"]["700"]
    assert stats["verified"] == 1
    assert stats["queried"] == 0
    assert entry["status"] == "verified_cached"
    assert entry["observed_date"] == "2026-09-07"
    assert entry["heating"] == "개별난방" and entry["parking_per_unit"] == 0.77


def test_gateway_error_stops_remaining_live_queries(tmp_path, monkeypatch):
    dataset_path = tmp_path / "dataset.json"
    cache_path = tmp_path / "kapt.json"
    dataset_path.write_text(json.dumps({"complexes": [
        {"complex_no": "1", "name": "A", "gu": "노원", "units": 100, "built_year": 2000},
        {"complex_no": "2", "name": "B", "gu": "노원", "units": 100, "built_year": 2000},
    ]}, ensure_ascii=False), encoding="utf-8")
    calls = []
    monkeypatch.setattr(kf, "clear_last_service_error", lambda: None)
    monkeypatch.setattr(kf, "last_service_error_code", lambda: "04")
    monkeypatch.setattr(kf, "_resolve_kapt_basis", lambda *args, **kwargs: calls.append(args[0]))
    stats = kf.refresh_kapt_facilities(
        dataset_path, cache_path, today="2026-09-22", key="secret", checkpoint_every=0,
    )
    assert calls == ["A"]
    assert stats["queried"] == 1 and stats["service_error"] is True


def test_coverage_gate_ignores_complexes_removed_from_public_pool(tmp_path, monkeypatch):
    dataset_path = tmp_path / "dataset.json"
    cache_path = tmp_path / "kapt.json"
    rows = [
        {"complex_no": "1", "name": "유지", "gu": "노원", "units": 100, "built_year": 2000},
        {"complex_no": "2", "name": "제외", "gu": "노원", "units": 100, "built_year": 2000},
    ]
    dataset_path.write_text(json.dumps({"complexes": rows}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(kf, "_resolve_kapt_basis", lambda name, *args, **kwargs: (
        f"K{name}", {"heating": "개별난방", "parking_ground": 0, "parking_underground": 100,
                     "parking_total": 100, "parking_household_count": 100, "parking_per_unit": 1.0},
    ))
    kf.refresh_kapt_facilities(
        dataset_path, cache_path, today="2026-09-22", key="secret", checkpoint_every=0,
    )

    dataset_path.write_text(json.dumps({"complexes": rows[:1]}, ensure_ascii=False), encoding="utf-8")
    stats = kf.refresh_kapt_facilities(
        dataset_path, cache_path, today="2026-09-23", key="secret", checkpoint_every=0,
    )
    cached = json.loads(cache_path.read_text(encoding="utf-8"))["complexes"]
    assert stats["verified"] == 1
    assert set(cached) == {"1"}
