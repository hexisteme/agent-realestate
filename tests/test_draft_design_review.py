from __future__ import annotations

import datetime
import hashlib
import json

import pytest

from blog import draft_design_review as ddr
from blog.tistory_design_review import grade_design_review, load_design_review, resolve_design_review_path

NOW = datetime.datetime(2026, 10, 11, 7, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))


def _draft(tmp_path, body="<p>원고</p>"):
    path = tmp_path / "report/blog/tistory/2026-10-11-tistory-draft.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _violation(rule, *nodes):
    return {"id": rule, "impact": "serious", "help": f"{rule} help", "tags": ["wcag2aa"],
            "nodes": [{"target": target, "in_draft": in_draft, "summary": "Fix: contrast 3.2:1"}
                      for target, in_draft in nodes]}


def _measurement(violations_390=(), violations_1440=(), scroll_390=390, overflowing=()):
    return {"host_url": "https://floker.tistory.com/115", "viewports": [
        {"label": "390x844", "width": 390, "height": 844, "document_scroll_width": scroll_390,
         "client_width": 390, "overflowing": list(overflowing), "screenshot": "390.png",
         "axe": {"version": "4.10.2", "violations": list(violations_390)}},
        {"label": "1440x900", "width": 1440, "height": 900, "document_scroll_width": 1440,
         "client_width": 1440, "overflowing": [], "screenshot": "1440.png",
         "axe": {"version": "4.10.2", "violations": list(violations_1440)}},
    ]}


def _advisory(**overrides):
    finding = {"priority": "P2", "kind": "suggestion", "check": "advisory", "certainty": "medium",
               "location": "h2:nth-of-type(1)", "evidence_source": "원고(HTML)", "observed": "제목 없이 표가 이어짐",
               "interpretation": "훑어보기 어려움", "impact": "모바일 독자", "recommended_fix": "소제목 추가",
               "verification": "다음 원고 재리뷰", "unverified": "없음"}
    finding.update(overrides)
    return finding


def _never(*_args):
    raise AssertionError("현재 리뷰가 있으면 측정·해석을 다시 부르지 않는다")


def test_axe_violation_in_draft_is_p1_rule_defect_and_outside_is_p2():
    measurement = _measurement(
        violations_390=[_violation("color-contrast", ("td[data-district='강남구']", True),
                                   (".another_category th", False))],
        violations_1440=[_violation("color-contrast", ("td[data-district='강남구']", True))])

    findings = ddr.derive_rule_findings(measurement)

    assert [(f["id"], f["priority"], f["kind"], f["check"], f["status"], f["certainty"]) for f in findings] == [
        ("B-1", "P1", "defect", "rule", "open", "high"),
        ("B-2", "P2", "defect", "rule", "open", "high"),
    ]
    assert "390x844 1노드" in findings[0]["observed"] and "1440x900 1노드" in findings[0]["observed"]
    assert "td[data-district='강남구']" in findings[0]["location"]
    assert ".another_category th" in findings[1]["location"]


def test_page_overflow_caused_by_draft_is_p1():
    measurement = _measurement(scroll_390=812, overflowing=[{"selector": "table.heat", "right": 812, "in_draft": True}])

    findings = ddr.derive_rule_findings(measurement)

    assert len(findings) == 1
    assert (findings[0]["priority"], findings[0]["check"]) == ("P1", "rule")
    assert "812" in findings[0]["observed"] and "table.heat" in findings[0]["location"]


def test_clean_measurement_has_no_rule_findings():
    assert ddr.derive_rule_findings(_measurement()) == []


def test_interpretation_cannot_block_publication():
    raw = [_advisory(priority="P1", kind="defect", check="rule", status="fixed"), _advisory(priority="P9"), "x"]

    findings, notes = ddr.normalize_advisory_findings(raw, first_number=3)

    assert len(findings) == 1
    assert (findings[0]["id"], findings[0]["priority"], findings[0]["check"], findings[0]["status"]) == (
        "B-3", "P2", "advisory", "open")
    assert "P2" in findings[0]["unverified"]
    assert len(notes) == 2


def test_written_review_binds_draft_and_feeds_gate(tmp_path):
    draft = _draft(tmp_path)
    blocked = _measurement(violations_390=[_violation("color-contrast", ("td.x", True))])

    outcome = ddr.author_design_review(draft, measure=lambda _p: blocked, interpret=lambda _p, _m: [_advisory()], now=NOW)

    review_path = resolve_design_review_path(draft)
    review = load_design_review(review_path.read_bytes())
    assert outcome.result == "WRITTEN"
    assert review["asset_sha256"] == hashlib.sha256(draft.read_bytes()).hexdigest()
    assert review["reviewer"] == ddr.REVIEWER
    assert [f["check"] for f in review["findings"]] == ["rule", "advisory"]
    assert review["coverage"]["inspected"] and review["coverage"]["tools"]
    assert grade_design_review(review_path, draft_path=draft).result == "BLOCK"

    review_path.unlink()
    ddr.author_design_review(draft, measure=lambda _p: _measurement(), interpret=lambda _p, _m: [], now=NOW)
    assert grade_design_review(review_path, draft_path=draft).result == "PASS"


def test_current_review_is_reused_without_new_calls(tmp_path):
    draft = _draft(tmp_path)
    ddr.author_design_review(draft, measure=lambda _p: _measurement(), interpret=lambda _p, _m: [], now=NOW)

    outcome = ddr.author_design_review(draft, measure=_never, interpret=_never, now=NOW)

    assert outcome.result == "SKIPPED"


def test_stale_own_review_is_rewritten(tmp_path):
    draft = _draft(tmp_path)
    ddr.author_design_review(draft, measure=lambda _p: _measurement(), interpret=lambda _p, _m: [], now=NOW)
    draft.write_text("<p>다시 생성된 원고</p>", encoding="utf-8")

    outcome = ddr.author_design_review(draft, measure=lambda _p: _measurement(), interpret=lambda _p, _m: [], now=NOW)

    review = json.loads(resolve_design_review_path(draft).read_text(encoding="utf-8"))
    assert outcome.result == "WRITTEN"
    assert review["asset_sha256"] == hashlib.sha256(draft.read_bytes()).hexdigest()


@pytest.mark.parametrize("content", [
    json.dumps({"schema": "design-review/v1", "reviewer": "claude-code:design-director", "findings": []}),
    "{broken",
])
def test_review_from_another_reviewer_is_left_alone(tmp_path, content):
    draft = _draft(tmp_path)
    review_path = resolve_design_review_path(draft)
    review_path.write_text(content, encoding="utf-8")

    outcome = ddr.author_design_review(draft, measure=_never, interpret=_never, now=NOW)

    assert outcome.result == "SKIPPED"
    assert review_path.read_text(encoding="utf-8") == content


def test_interpretation_failure_keeps_measured_review(tmp_path):
    draft = _draft(tmp_path)

    def broken(_p, _m):
        raise RuntimeError("codex rc=1")

    outcome = ddr.author_design_review(
        draft, measure=lambda _p: _measurement(violations_390=[_violation("link-in-text-block", ("p a", True))]),
        interpret=broken, now=NOW)

    review = json.loads(resolve_design_review_path(draft).read_text(encoding="utf-8"))
    assert outcome.result == "WRITTEN"
    assert [f["check"] for f in review["findings"]] == ["rule"]
    assert any("codex rc=1" in item for item in review["coverage"]["not_inspected"])


def test_measurement_failure_writes_nothing(tmp_path):
    draft = _draft(tmp_path)

    def offline(_p):
        raise OSError("network down")

    outcome = ddr.author_design_review(draft, measure=offline, interpret=_never, now=NOW)

    assert outcome.result == "FAILED" and "network down" in outcome.detail
    assert not resolve_design_review_path(draft).exists()


def test_host_url_is_latest_published_daily(tmp_path):
    publications = tmp_path / "report/blog/publications"
    publications.mkdir(parents=True)
    (publications / "2026-10-09-daily.json").write_text(json.dumps({"url": "https://floker.tistory.com/114"}))
    (publications / "2026-10-10-daily.json").write_text(json.dumps({"url": "https://floker.tistory.com/115"}))
    (publications / "2026-10-10-periodic.json").write_text(json.dumps({"url": "https://floker.tistory.com/999"}))

    assert ddr.find_host_url(tmp_path) == "https://floker.tistory.com/115"


def test_measured_body_is_what_the_publisher_posts(tmp_path, monkeypatch):
    draft = _draft(tmp_path, "<textarea id='b'>래퍼</textarea>")
    monkeypatch.setattr(ddr, "parse_helper", lambda path: {"title": "t", "body": "<p>게시 본문</p>", "tags": ""})
    assert ddr.extract_published_body(draft) == "<p>게시 본문</p>"

    monkeypatch.setattr(ddr, "parse_helper", lambda path: {"title": "t", "body": "  ", "tags": ""})
    with pytest.raises(ValueError):
        ddr.extract_published_body(draft)
