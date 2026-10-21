from __future__ import annotations

import datetime
import hashlib
import json

import pytest

from blog import draft_design_review as ddr
from blog.tistory_design_review import grade_design_review, load_design_review, resolve_design_review_path

NOW = datetime.datetime(2026, 10, 11, 7, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
SITE_BASE = "https://hexisteme.github.io/seoul-re-snapshot"
LOADED = {"total": 1, "loaded": 1, "failed": []}
SKIN_WAIVER = {
    "id": "B-8", "priority": "P2", "kind": "defect", "check": "rule", "status": "waived", "certainty": "high",
    "location": "스킨·플랫폼 위젯(본문 밖)", "evidence_source": "렌더된 웹 페이지(브라우저 자동화)",
    "observed": "axe-core 4.10.2 link-name: 390x844 1노드.", "targets": [".tt_wrap_thumb"], "interpretation": "스킨 위젯 위반",
    "impact": "보조기기 사용자", "recommended_fix": "스킨에서 고친다", "verification": "axe 재실행", "unverified": "없음",
    "waiver_reason": "남은 link-name `.tt_wrap_thumb` 1건은 Tistory 스크립트가 런타임에 넣는 요소다. 승인: 사용자, 2026-10-10.",
}


def _draft(tmp_path, body="<p>원고</p>"):
    path = tmp_path / "report/blog/tistory/2026-10-11-tistory-draft.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _violation(rule, *nodes):
    return {"id": rule, "impact": "serious", "help": f"{rule} help", "tags": ["wcag2aa"],
            "nodes": [{"target": target, "in_draft": in_draft, "summary": "Fix: contrast 3.2:1"}
                      for target, in_draft in nodes]}


def _measurement(violations_390=(), violations_1440=(), scroll_390=390, overflowing=(), incomplete_390=(),
                 images=LOADED):
    return {"host_url": "https://floker.tistory.com/115", "viewports": [
        {"label": "390x844", "width": 390, "height": 844, "document_scroll_width": scroll_390,
         "client_width": 390, "overflowing": list(overflowing), "screenshot": "390.png", "images": images,
         "axe": {"version": "4.10.2", "violations": list(violations_390), "incomplete": list(incomplete_390)}},
        {"label": "1440x900", "width": 1440, "height": 900, "document_scroll_width": 1440,
         "client_width": 1440, "overflowing": [], "screenshot": "1440.png", "images": images,
         "axe": {"version": "4.10.2", "violations": list(violations_1440), "incomplete": []}},
    ]}


def _review(draft):
    return json.loads(resolve_design_review_path(draft).read_text(encoding="utf-8"))


def _baseline(tmp_path, **overrides):
    path = tmp_path / "design-review/20261009/floker-114.design_review.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": "design-review/v1", "domain": "blog", "mode": "review", "asset_ref": "https://floker.tistory.com/114",
        "asset_sha256": None, "reviewed_at": "2026-10-10T12:00:00+09:00", "reviewer": "claude-code:design-director",
        "findings": [{**SKIN_WAIVER, **overrides}],
        "coverage": {"inspected": ["390x844 렌더"], "not_inspected": [], "tools": ["Playwright"], "handoff": []},
    }, ensure_ascii=False), encoding="utf-8")
    return path


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


def test_only_axe_findings_carry_targets(tmp_path):
    measurement = _measurement(violations_390=[_violation("link-name", (".tt_wrap_thumb", False))], scroll_390=812)

    findings = ddr.derive_rule_findings(measurement)

    assert sorted((f["observed"].split()[0], f.get("targets")) for f in findings) == [
        ("axe-core", [".tt_wrap_thumb"]), ("document", None)]
    draft = _draft(tmp_path)
    ddr.author_design_review(draft, measure=lambda _p: measurement, interpret=lambda _p, _m: [], now=NOW)
    grade = grade_design_review(resolve_design_review_path(draft), draft_path=draft)
    assert grade.result == "PASS", grade.detail


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


def test_rule_findings_carry_stable_fingerprint():
    measurement = _measurement(
        violations_390=[_violation("color-contrast", ("td[data-district='강남구']", True),
                                   ('th > a[href="/131"]', False), ('a[href="/129"]', False),
                                   ('a[href="/127"]', False), (".another_category th", False))],
        scroll_390=812, overflowing=[{"selector": "table.heat", "right": 812, "in_draft": True}])

    findings = ddr.derive_rule_findings(measurement)

    assert sorted(finding["fingerprint"] for finding in findings) == [
        "blog:axe/color-contrast:draft:td[data-district='강남구']",
        'blog:axe/color-contrast:skin:.another_category th,a[href="/#"],th > a[href="/#"]',
        "blog:overflow:draft:table.heat",
    ]
    assert any(" 외 1" in finding["location"] for finding in findings)
    skin = next(finding for finding in findings if ":skin:" in finding["fingerprint"])
    assert skin["targets"] == ['th > a[href="/131"]', 'a[href="/129"]', 'a[href="/127"]', ".another_category th"]


def test_advisory_topic_and_key_are_its_fingerprint():
    findings, _ = ddr.normalize_advisory_findings(
        [_advisory(topic="table-readability", key="Wide Heat_Table!"), _advisory(topic="made-up", key="x"),
         _advisory(key="표"), _advisory(key="", observed="다른 관찰")], first_number=1)

    assert [finding["fingerprint"] for finding in findings[:2]] == [
        "blog:advisory/table-readability/wide-heat-table", "blog:advisory/other/x"]
    assert all(finding["fingerprint"].startswith("blog:advisory/other/text-") for finding in findings[2:])
    assert findings[2]["fingerprint"] != findings[3]["fingerprint"] and "key" in findings[2]["unverified"]
    assert "topic" not in findings[0] and "key" not in findings[0]


def test_interpreter_is_told_previous_advisory_keys(tmp_path):
    draft = _draft(tmp_path)
    folder = draft.parent
    previous = {"reviewer": ddr.REVIEWER, "findings": [
        {"fingerprint": "blog:advisory/table-readability/wide-heat-table", "observed": "히트맵 표가 넓다"},
        {"fingerprint": "blog:axe/link-name:skin:.tt_wrap_thumb", "observed": "axe"}]}
    other = {"reviewer": "claude-code:design-director", "findings": [
        {"fingerprint": "blog:advisory/reading-flow/human-only", "observed": "사람 리뷰"}]}
    (folder / "2026-10-10-tistory-draft.design_review.json").write_text(json.dumps(previous), encoding="utf-8")
    (folder / "2026-10-10-tistory-draft.b.design_review.json").write_text(json.dumps(other), encoding="utf-8")
    (folder / "2026-10-09-tistory-draft.design_review.json").write_text(
        json.dumps({"reviewer": ddr.REVIEWER, "findings": 1}), encoding="utf-8")
    (folder / "2026-10-12-tistory-draft.design_review.json").write_text(
        json.dumps({**previous, "findings": [{"fingerprint": "blog:advisory/other/later", "observed": "뒤"}]}),
        encoding="utf-8")

    prompt = ddr.build_advisory_prompt(draft, _measurement(), evidence_dir=tmp_path)

    assert "table-readability/wide-heat-table" in prompt and "히트맵 표가 넓다" in prompt
    assert "human-only" not in prompt and "other/later" not in prompt and "tt_wrap_thumb" not in prompt


def test_axe_incomplete_is_listed_not_judged(tmp_path):
    draft = _draft(tmp_path)
    measurement = _measurement(incomplete_390=[_violation("color-contrast", ("td.x", True), (".skin p", False))])

    ddr.author_design_review(draft, measure=lambda _p: measurement, interpret=lambda _p, _m: [], now=NOW)

    review = _review(draft)
    assert review["findings"] == [] and review["coverage"]["complete"] is True
    assert any("incomplete" in item and "color-contrast" in item and "390x844 2노드(원고 1)" in item
               for item in review["coverage"]["not_inspected"])


def _codex_failure(_p, _m):
    raise RuntimeError("codex rc=1")


@pytest.mark.parametrize("interpret, images, cause", [
    (_codex_failure, LOADED, "codex rc=1"),
    (lambda _p, _m: ["형식 아님"], LOADED, "버림"),
    (lambda _p, _m: [], {"total": 1, "loaded": 0, "failed": [f"{SITE_BASE}/images/a.png"]}, "이미지"),
    (lambda _p, _m: [], None, "이미지 로드 상태"),
])
def test_partial_review_names_the_failed_layer_first(tmp_path, interpret, images, cause):
    draft = _draft(tmp_path)
    measurement = _measurement(images=images)
    if images is None:
        for viewport in measurement["viewports"]:
            del viewport["images"]

    ddr.author_design_review(draft, measure=lambda _p: measurement, interpret=interpret, now=NOW)

    coverage = _review(draft)["coverage"]
    assert coverage["complete"] is False and cause in coverage["not_inspected"][0]
    grade = grade_design_review(resolve_design_review_path(draft), draft_path=draft)
    assert grade.result == "PASS" and "부분 리뷰" in grade.detail


def test_complete_review_says_so(tmp_path):
    draft = _draft(tmp_path)

    ddr.author_design_review(draft, measure=lambda _p: _measurement(), interpret=lambda _p, _m: [], now=NOW)

    assert _review(draft)["coverage"]["complete"] is True


def test_interpreter_is_told_unloaded_images_are_environment(tmp_path):
    failed = _measurement(images={"total": 2, "loaded": 1, "failed": [f"{SITE_BASE}/images/a.png"]})

    prompt = ddr.build_advisory_prompt(_draft(tmp_path), failed, evidence_dir=tmp_path)

    assert f"{SITE_BASE}/images/a.png" in prompt and "환경" in prompt
    assert "topic" in prompt


def test_deployed_site_assets_resolve_to_local_build(tmp_path):
    site = tmp_path / "site"
    (site / "images").mkdir(parents=True)
    (site / "images/a b.png").write_bytes(b"png")
    (tmp_path / "secret.png").write_bytes(b"x")

    def local(url):
        return ddr.local_site_asset(url, site_dir=site, base_url=SITE_BASE)

    assert local(f"{SITE_BASE}/images/a%20b.png?v=1") == (site / "images/a b.png").resolve()
    for url in (f"{SITE_BASE}/images/missing.png", f"{SITE_BASE}/../secret.png", f"{SITE_BASE}/images/%2e%2e/%2e%2e/secret.png",
                "https://other.example/seoul-re-snapshot/images/a%20b.png", f"{SITE_BASE}-evil/images/a%20b.png"):
        assert local(url) is None, url


def test_human_waiver_is_inherited_from_baseline(tmp_path):
    draft = _draft(tmp_path)
    baseline = _baseline(tmp_path)
    measurement = _measurement(violations_390=[_violation("link-name", (".tt_wrap_thumb", False))])

    ddr.author_design_review(draft, measure=lambda _p: measurement, interpret=lambda _p, _m: [], now=NOW,
                             baselines=ddr.load_waiver_baselines([tmp_path / "design-review"]))

    finding = _review(draft)["findings"][0]
    assert (finding["status"], finding["waiver_ref"]) == ("waived", f"{baseline}#B-8")
    assert finding["waiver_reason"] == SKIN_WAIVER["waiver_reason"]
    assert "waived_by" not in finding and "waived_at" not in finding
    grade = grade_design_review(resolve_design_review_path(draft), draft_path=draft)
    assert grade.result == "PASS" and "waived 1" in grade.detail


def test_inherited_waiver_copies_the_human_approval(tmp_path):
    draft = _draft(tmp_path)
    _baseline(tmp_path, waived_by="사용자", waived_at="2026-10-10T12:00:00+09:00")
    measurement = _measurement(violations_390=[_violation("link-name", (".tt_wrap_thumb", False))])

    ddr.author_design_review(draft, measure=lambda _p: measurement, interpret=lambda _p, _m: [], now=NOW,
                             baselines=ddr.load_waiver_baselines([tmp_path / "design-review"]))

    finding = _review(draft)["findings"][0]
    assert (finding["waived_by"], finding["waived_at"]) == ("사용자", "2026-10-10T12:00:00+09:00")


@pytest.mark.parametrize("overrides, targets", [
    ({"waived_by": "codex:exec"}, (".tt_wrap_thumb",)),
    ({"observed": "button-name .button-menu(390), link-name .tt_wrap_thumb", "targets": None}, (".tt_wrap_thumb",)),
    ({"targets": [".tt_wrap_thumb", ".x"]}, (".tt_wrap_thumb",)),
    ({}, (".tt_wrap_thumb", ".new_widget a")),
])
def test_unapproved_unscoped_or_wider_findings_stay_open(tmp_path, overrides, targets):
    draft = _draft(tmp_path)
    _baseline(tmp_path, **overrides)
    measurement = _measurement(violations_390=[_violation("link-name", *((target, False) for target in targets))])

    ddr.author_design_review(draft, measure=lambda _p: measurement, interpret=lambda _p, _m: [], now=NOW,
                             baselines=ddr.load_waiver_baselines([tmp_path / "design-review"]))

    finding = _review(draft)["findings"][0]
    assert finding["status"] == "open" and "waiver_ref" not in finding


def test_inheritance_failure_makes_the_review_partial(tmp_path, monkeypatch):
    draft = _draft(tmp_path)
    _baseline(tmp_path)
    measurement = _measurement(violations_390=[_violation("link-name", (".tt_wrap_thumb", False))])

    def broken():
        raise ImportError("validator moved")

    monkeypatch.setattr(ddr, "load_validator", broken)
    ddr.author_design_review(draft, measure=lambda _p: measurement, interpret=lambda _p, _m: [], now=NOW,
                             baselines=ddr.load_waiver_baselines([tmp_path / "design-review"]))

    review = _review(draft)
    assert review["findings"][0]["status"] == "open"
    assert review["coverage"]["complete"] is False and "승계" in review["coverage"]["not_inspected"][0]
    assert not any("기준선 대조" in item for item in review["coverage"]["inspected"])


def test_waiver_baselines_are_floker_reviews_that_read(tmp_path):
    baseline = _baseline(tmp_path)
    (baseline.parent / "floker-115.design_review.json").write_text("{", encoding="utf-8")
    (baseline.parent / "floker-116.design_review.json").write_text("[]", encoding="utf-8")
    (baseline.parent / "slkim5943-34.design_review.json").write_text(baseline.read_text(encoding="utf-8"),
                                                                       encoding="utf-8")
    (baseline.parent / "floker-117.json").write_text("{}", encoding="utf-8")

    assert [path for path, _ in ddr.load_waiver_baselines([tmp_path / "design-review", baseline])] == [str(baseline)]


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
