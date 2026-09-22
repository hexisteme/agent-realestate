from __future__ import annotations

import json
from types import SimpleNamespace

import blog.collect_listing_inventory as collector
from agent_realestate.collectors.naver_region import DEFAULT_SCRIPT, RegionComplex


def _row(no: str, sale: int, lease: int) -> RegionComplex:
    return RegionComplex(
        no, f"단지{no}", 100, "200001", 100,
        deal_count=sale, lease_count=lease, rent_count=1, short_term_rent_count=0,
    )


def test_default_scanner_uses_codex_runtime_path():
    assert "/.codex/scripts/naver-region-scan.sh" in DEFAULT_SCRIPT
    assert "/.claude/" not in DEFAULT_SCRIPT


def test_method_v3_uses_legal_dong_complex_list_not_bbox_markers():
    js = collector._inventory_js("1135000000")
    assert "/api/regions/complexes?cortarNo=" in js
    assert "&realEstateType=APT&order=rank" in js
    assert "rentCount" in js and "shortTermRentCount" in js
    assert "AbortController" in js
    assert "BATCH_SIZE=6" in js
    assert 'open("GET"' not in js
    assert "single-markers" not in js


def test_discover_tab_pattern_chooses_newest_responsive_naver_tab(monkeypatch):
    listing = "\n".join([
        "[1.1] old <https://new.land.naver.com/complexes?ms=old>",
        "[1.2] new <https://new.land.naver.com/complexes?ms=new>",
    ])
    monkeypatch.setattr(
        collector.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=listing),
    )
    calls = []

    def run_expression(script, candidate, expression, *, timeout):
        calls.append(candidate)
        return "네이버페이 부동산"

    monkeypatch.setattr(collector, "_run_expression", run_expression)
    assert collector.discover_tab_pattern("/fake/read.sh").endswith("ms=new")
    assert len(calls) == 1


def test_prepare_naver_tab_keeps_existing_tab_without_seed_or_wait():
    discovered = []
    seeded = []
    waited = []

    tab, diagnostic = collector.prepare_naver_tab(
        "/fake/read.sh",
        discover=lambda script: discovered.append(script) or "https://new.land.naver.com/existing",
        seed=lambda: seeded.append(True) or True,
        sleeper=lambda seconds: waited.append(seconds),
    )

    assert tab == "https://new.land.naver.com/existing"
    assert diagnostic is None
    assert discovered == ["/fake/read.sh"]
    assert seeded == [] and waited == []


def test_prepare_naver_tab_seeds_once_then_rediscovers_with_bounded_wait():
    discoveries = iter([None, "https://new.land.naver.com/seeded"])
    seeded = []
    waited = []

    tab, diagnostic = collector.prepare_naver_tab(
        "/fake/read.sh", discover=lambda _script: next(discoveries),
        seed=lambda: seeded.append(True) or True,
        sleeper=lambda seconds: waited.append(seconds), wait_seconds=0.25,
    )

    assert tab == "https://new.land.naver.com/seeded"
    assert diagnostic is None
    assert seeded == [True]
    assert waited == [0.25]


def test_prepare_does_not_seed_when_tab_discovery_is_unavailable(monkeypatch):
    monkeypatch.setattr(collector, "_discover_tab_pattern_detail", lambda _script: (None, False))
    seeded = []

    tab, diagnostic = collector.prepare_naver_tab("/fake/read.sh", seed=lambda: seeded.append(True) or True)

    assert tab is None and diagnostic == "NAVER_TAB_DISCOVERY_UNAVAILABLE"
    assert seeded == []


def test_seed_failure_is_single_safe_command_and_never_prints_raw_output(capsys):
    calls = []

    def failing_runner(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1, stdout="secret-token=should-not-print", stderr="sensitive")

    assert collector.seed_naver_tab(runner=failing_runner) is False
    assert calls == [(
        (["open", "-g", "-a", "Google Chrome", "https://new.land.naver.com/"],),
        {"capture_output": True, "text": True, "timeout": collector.NAVER_SEED_TIMEOUT_SECONDS},
    )]
    assert capsys.readouterr().out == ""

    tab, diagnostic = collector.prepare_naver_tab(
        "/fake/read.sh", discover=lambda _script: None, seed=lambda: False,
    )
    assert tab is None and diagnostic == "NAVER_TAB_SEED_UNAVAILABLE"
    assert "secret-token" not in diagnostic

    def timeout_runner(*_args, **_kwargs):
        raise collector.subprocess.TimeoutExpired("open", 1, output="secret-token=timeout")

    assert collector.seed_naver_tab(runner=timeout_runner) is False
    assert "secret-token" not in capsys.readouterr().out


def test_scan_gu_inventory_polls_async_result_without_raw_error_output(monkeypatch):
    payload = [{
        "complexNo": "1", "name": "단지1", "far": 0, "builtYm": "200001",
        "households": 100, "dealCount": 4, "leaseCount": 2,
        "rentCount": 1, "shortTermRentCount": 0,
    }]
    outputs = iter([
        "STARTED",
        json.dumps({"state": "pending"}),
        json.dumps({"state": "done", "payload": payload}),
        "CLEARED",
    ])
    monkeypatch.setattr(collector, "_run_expression", lambda *args, **kwargs: next(outputs))
    monkeypatch.setattr(collector.time, "sleep", lambda _seconds: None)

    rows = collector.scan_gu_inventory("노원", tab_pattern="ms=inventory")
    assert len(rows) == 1
    assert rows[0].district == "노원"
    assert rows[0].deal_count == 4


def test_collect_scans_unfiltered_scope_and_assigns_actual_gu():
    calls = []

    def scanner(gu, **kwargs):
        calls.append((gu, kwargs))
        return [_row("1", 4, 2)] if gu == "노원" else [_row("2", 3, 1)]

    snap = collector.collect_inventory_snapshot(
        None, observed_at="2026-09-22T07:05:00+09:00",
        districts=("노원", "도봉"), script="/fake/script",
        tab_pattern="ms=inventory", scanner=scanner,
    )

    assert snap["complete"] is True
    assert snap["districts"]["노원"]["sale_article_count"] == 4
    assert snap["districts"]["도봉"]["sale_article_count"] == 3
    assert [call[0] for call in calls] == ["노원", "도봉"]
    assert all(call[1]["script"] == "/fake/script" for call in calls)
    assert all(call[1]["tab_pattern"] == "ms=inventory" for call in calls)


def test_empty_district_response_is_saved_as_incomplete_evidence():
    def scanner(gu, **kwargs):
        return [_row("1", 4, 2)] if gu == "노원" else []

    snap = collector.collect_inventory_snapshot(
        None, observed_at="2026-09-22", districts=("노원", "도봉"),
        script="/fake/script", scanner=scanner,
    )
    assert snap["complete"] is False
    assert {item["kind"] for item in snap["incomplete_reasons"]} == {"scan_failed"}


def test_successful_district_checkpoint_resumes_same_day(tmp_path):
    calls = []

    def scanner(gu, **kwargs):
        calls.append(gu)
        return [_row("1", 4, 2)]

    first = collector.collect_inventory_snapshot(
        observed_at="2026-09-22T07:05:00+09:00", districts=("노원",),
        scanner=scanner, checkpoint_dir=tmp_path,
    )
    second = collector.collect_inventory_snapshot(
        observed_at="2026-09-22T08:05:00+09:00", districts=("노원",),
        scanner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must resume")),
        checkpoint_dir=tmp_path,
    )

    assert first["complete"] is True and second["complete"] is True
    assert calls == ["노원"]
    assert second["districts"]["노원"] == first["districts"]["노원"]
    assert (tmp_path / "노원.json").stat().st_mode & 0o777 == 0o600


def test_main_fails_loudly_before_scan_when_required_file_missing(tmp_path, capsys):
    rc = collector.main([
        "--today", "2026-09-22", "--script", str(tmp_path / "missing.sh"),
    ])
    assert rc == 2
    assert "Chrome 읽기 스크립트 없음" in capsys.readouterr().out
