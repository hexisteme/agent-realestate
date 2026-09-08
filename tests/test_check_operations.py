"""운영 점검은 오류 목록뿐 아니라 실제 지표·기준일·카드·아카이브를 검증한다."""
import json
from datetime import date

from blog.check_operations import check_operations
from blog.macro_context import build_macro_context


def _outputs(tmp_path):
    source, site = tmp_path / "source", tmp_path / "site"
    (source / "snapshots/macro").mkdir(parents=True)
    site.mkdir()
    ds = {"generated": "2026-09-08", "data_asof": "2026-09-08", "count": 1, "complexes": [{}]}
    for p in (source / "snapshots/dataset-2026-09-08.json", site / "dataset.json"):
        p.write_text(json.dumps(ds))
    item = {"code": "kr_m2", "label": "한국 M2(평잔·계절조정)", "unit": "십억원", "freq": "monthly",
            "source": "한국은행 ECOS 161Y005/BBHS00", "url": "https://ecos.bok.or.kr/",
            "series_kind": "observations", "value": 4212955.4, "date": "2026-06-01", "since": "2026-06-01",
            "prev_value": 4183579.2, "prev_date": "2026-05-01", "since_window_start": False,
            "series": [["2025-06-01", 3975400], ["2026-05-01", 4183579.2], ["2026-06-01", 4212955.4]]}
    macro = {"asof": "2026-09-08", "indicators": {"kr_m2": item}, "errors": {}, "n_ok": 1, "carried": {}}
    mp = source / "snapshots/macro/macro-2026-09-08.json"
    mp.write_text(json.dumps(macro))
    (site / "macro.html").write_text(build_macro_context(macro, "2026-09-08")["page_html"])
    for name in ("calc", "cycles"):
        (site / f"{name}.html").write_text("<html>기존 페이지</html>")
    return source, site, macro, mp


def _check(source, site, **kwargs):
    return check_operations("2026-09-08", source, site, today=date(2026, 9, 8), **kwargs)


def test_success_then_missing_indicator_with_empty_errors_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("ECOS_API_KEY", raising=False)
    source, site, macro, mp = _outputs(tmp_path)
    result = _check(source, site, expected_count=1, m2_month="2026-06")
    assert result["status"] == "PASS" and result["key_scan"] == "NOT_CHECKED_KEY_NOT_LOADED"
    macro["indicators"] = {}; macro["n_ok"] = 0
    mp.write_text(json.dumps(macro))
    result = _check(source, site)
    assert result["status"] == "FAIL"
    assert not next(c["passed"] for c in result["checks"] if c["name"] == "kr_m2_present")


def test_snapshot_success_requires_updated_card_and_no_key(tmp_path, monkeypatch):
    source, site, _, _ = _outputs(tmp_path)
    monkeypatch.setenv("ECOS_API_KEY", "synthetic-secret-for-test")
    (site / "macro.html").write_text("<p>과거 화면 synthetic-secret-for-test</p>")
    result = _check(source, site)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert {"kr_m2_card", "no_ecos_key"} <= failed
    assert "synthetic-secret-for-test" not in json.dumps(result)


def test_card_value_requires_equal_cell_not_substring(tmp_path):
    source, site, _, _ = _outputs(tmp_path)
    p = site / "macro.html"
    p.write_text(p.read_text().replace("4,213.0조원", "14,213.0조원"))
    result = _check(source, site)
    assert result["status"] == "FAIL"
    assert not next(c["passed"] for c in result["checks"] if c["name"] == "kr_m2_card")


def test_future_check_is_not_due_and_does_not_read(tmp_path):
    result = check_operations("2026-09-16", tmp_path / "absent", tmp_path / "absent",
                              m2_month="2026-07", today=date(2026, 9, 8))
    assert result["status"] == "NOT_DUE" and result["checks"] == []


def test_stale_month_and_missing_filing_archives_are_failures(tmp_path):
    source, site, _, _ = _outputs(tmp_path)
    result = _check(source, site, m2_month="2026-07", filing_base="2026-09-06")
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "kr_m2_month" in failed and "molit_archive_2026-09-06" in failed


def test_invalid_json_is_reported_without_raw_contents(tmp_path):
    source, site, _, mp = _outputs(tmp_path)
    mp.write_text("sensitive-invalid-json")
    result = _check(source, site)
    assert result["status"] == "FAIL" and "sensitive-invalid-json" not in json.dumps(result)
