"""티스토리 발행 디자인 리뷰 게이트 — 합성 원고·합성 리뷰만, 브라우저·네트워크·텔레그램 실행 없음.

계약: ~/.agents/skills/design-director/references/contract.md §2·§6 (design-review/v1).
리뷰 부재는 사용자 결정으로 비차단이며 기존 발행 결과와 바이트 단위로 같아야 한다.
"""
import datetime
import hashlib
import html
import json
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from blog import tistory_design_review as gate
from blog import tistory_publish_pw as pub
from blog.tistory_delivery import read_delivery, write_delivery

# 판정 정본(design-director 검증기)은 이 저장소 밖에 있다 — 그 파일이 없는 환경(CI)에서는 판정 테스트를 건너뛴다.
requires_validator = pytest.mark.skipif(not gate.VALIDATOR_PATH.is_file(),
                                        reason=f"design-director 검증기 없음: {gate.VALIDATOR_PATH}")

DAY = "2026-09-11"
# 리뷰 fixture 의 기본 결속값 — write_review 가 원고 바이트 sha256 으로 바꾼다(계약 §6: 게이트용 리뷰는 결속 필수).
BIND = "<draft sha256>"
DATA = {"title": "합성 일간 — 2026-09-11", "body": "<p>합성 본문</p>", "tags": "합성"}


class BrowserBoundary(Exception):
    pass


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    class Today(datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 11)
    monkeypatch.setattr(pub.datetime, "date", Today)
    monkeypatch.setattr(pub, "PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(pub, "STATE_FILE", str(tmp_path / "profile/state.json"))
    monkeypatch.setattr(pub, "_notify_failure", Mock())
    monkeypatch.setattr(pub, "_verify_published_on_blog", Mock(return_value=False))
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = Mock(side_effect=BrowserBoundary)
    package = types.ModuleType("playwright")
    package.sync_api = api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    return tmp_path, api.sync_playwright


@pytest.fixture
def telegram(monkeypatch):
    """실제 .env·텔레그램 대신 같은 알림 경로의 끝점만 대체한다."""
    import agent_realestate.config as config
    import agent_realestate.notify.telegram as tg
    monkeypatch.setattr(config, "load_env_file", Mock())
    sent = Mock(return_value=True)
    monkeypatch.setattr(tg, "send_message", sent)
    return sent


def draft(root, day=DAY, kind="daily"):
    name = f"{day}-tistory-draft.html" if kind == "daily" else f"{day}-{kind}-tistory-draft.html"
    path = root / "report/blog/tistory" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"<textarea id={k}>{html.escape(v)}</textarea>"
                            for k, v in zip(("t", "b", "g"), DATA.values())))
    return path


def finding(**overrides):
    base = {"id": "B-1", "priority": "P2", "kind": "defect", "check": "rule", "status": "open",
            "certainty": "high", "location": "article table", "evidence_source": "렌더된 웹 페이지",
            "observed": "합성 관찰", "recommended_fix": "합성 수정", "verification": "재측정",
            "unverified": "없음"}
    base.update(overrides)
    return base


def review(findings=(), **overrides):
    base = {"schema": "design-review/v1", "domain": "blog", "mode": "review",
            "asset_ref": "report/blog/tistory/2026-09-11-tistory-draft.html", "asset_sha256": BIND,
            "reviewed_at": "2026-09-11T06:00:00+09:00", "reviewer": "claude-code:design-director",
            "findings": list(findings),
            "coverage": {"inspected": ["390x844 렌더"], "not_inspected": [], "tools": [], "handoff": []}}
    base.update(overrides)
    return base


def write_review(draft_path, payload, path=None):
    target = Path(path) if path else Path(str(draft_path)[:-len(".html")] + ".design_review.json")
    if isinstance(payload, dict) and payload.get("asset_sha256") == BIND:
        payload = {**payload, "asset_sha256": sha(draft_path)}
    target.write_text(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False))
    return target


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_bytes(root):
    return {p: p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}


# ── 경로 규약 ──────────────────────────────────────────────────────────────

def test_default_review_path_sits_next_to_draft_and_flag_overrides(tmp_path):
    path = tmp_path / "report/blog/tistory/2026-09-11-tistory-draft.html"
    assert gate.resolve_design_review_path(path) == (
        tmp_path / "report/blog/tistory/2026-09-11-tistory-draft.design_review.json")
    periodic = tmp_path / "report/blog/tistory/2026-09-13-periodic-tistory-draft.html"
    assert gate.resolve_design_review_path(periodic).name == "2026-09-13-periodic-tistory-draft.design_review.json"
    assert gate.resolve_design_review_path(path, tmp_path / "x.json") == tmp_path / "x.json"


# ── 등급 규칙(순수 판정) ─────────────────────────────────────────────────────

def test_absent_review_is_not_run_without_reading_draft(tmp_path):
    grade = gate.grade_design_review(tmp_path / "none.json", draft_path=tmp_path / "missing.html")
    assert grade.result == "NOT_RUN"
    assert "design review 미실행" in grade.detail


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe\x00", b"[]", b'"text"'])
def test_unreadable_json_awaits_human(tmp_path, raw):
    path = draft(tmp_path)
    target = Path(str(path)[:-5] + ".design_review.json")
    target.write_bytes(raw)
    assert gate.grade_design_review(target, draft_path=path).result == "AWAIT_HUMAN"


def test_review_path_that_cannot_be_read_awaits_human(tmp_path):
    path = draft(tmp_path)
    target = Path(str(path)[:-5] + ".design_review.json")
    target.mkdir()
    assert gate.grade_design_review(target, draft_path=path).result == "AWAIT_HUMAN"


@pytest.mark.parametrize("schema", ["design-review/v2", "", None])
def test_wrong_schema_awaits_human(tmp_path, schema):
    path = draft(tmp_path)
    target = write_review(path, review(schema=schema))
    assert gate.grade_design_review(target, draft_path=path).result == "AWAIT_HUMAN"


def test_missing_schema_awaits_human(tmp_path):
    path = draft(tmp_path)
    payload = review()
    del payload["schema"]
    target = write_review(path, payload)
    assert gate.grade_design_review(target, draft_path=path).result == "AWAIT_HUMAN"


@pytest.mark.parametrize("domain", ["shorts", "app", "Blog", None])
def test_domain_other_than_blog_awaits_human(tmp_path, domain):
    path = draft(tmp_path)
    target = write_review(path, review(domain=domain))
    grade = gate.grade_design_review(target, draft_path=path)
    assert grade.result == "AWAIT_HUMAN"
    assert "domain" in grade.detail


@pytest.mark.parametrize("field,value", [
    ("priority", "P0"), ("priority", "p1"), ("kind", "bug"), ("check", "manual"),
    ("status", "closed"), ("status", None),
])
def test_vocabulary_outside_contract_awaits_human(tmp_path, field, value):
    path = draft(tmp_path)
    target = write_review(path, review([finding(**{field: value})]))
    grade = gate.grade_design_review(target, draft_path=path)
    assert grade.result == "AWAIT_HUMAN"
    assert field in grade.detail


def test_missing_vocabulary_field_awaits_human(tmp_path):
    path = draft(tmp_path)
    item = finding()
    del item["check"]
    target = write_review(path, review([item]))
    assert gate.grade_design_review(target, draft_path=path).result == "AWAIT_HUMAN"


@pytest.mark.parametrize("findings", [{}, None, "B-1", 3])
def test_findings_not_a_list_awaits_human(tmp_path, findings):
    path = draft(tmp_path)
    target = write_review(path, review(findings=[], **{}) | {"findings": findings})
    assert gate.grade_design_review(target, draft_path=path).result == "AWAIT_HUMAN"


def test_findings_missing_or_item_not_object_awaits_human(tmp_path):
    path = draft(tmp_path)
    payload = review()
    del payload["findings"]
    assert gate.grade_design_review(write_review(path, payload), draft_path=path).result == "AWAIT_HUMAN"
    assert gate.grade_design_review(write_review(path, review(["B-1"])), draft_path=path).result == "AWAIT_HUMAN"


def test_stale_asset_sha256_awaits_human_even_over_blocking_finding(tmp_path):
    path = draft(tmp_path)
    blocking = [finding(priority="P1", check="rule")]
    target = write_review(path, review(blocking, asset_sha256="0" * 64))
    grade = gate.grade_design_review(target, draft_path=path)
    assert grade.result == "AWAIT_HUMAN"
    assert "오래된" in grade.detail


def test_non_string_asset_sha256_awaits_human(tmp_path):
    path = draft(tmp_path)
    target = write_review(path, review(asset_sha256=123))
    assert gate.grade_design_review(target, draft_path=path).result == "AWAIT_HUMAN"


@requires_validator
@pytest.mark.parametrize("asset", ["match", "upper"])
def test_matching_asset_sha256_is_not_stale(tmp_path, asset):
    path = draft(tmp_path)
    payload = review([finding(priority="P2")], asset_sha256={"match": sha(path), "upper": sha(path).upper()}[asset])
    assert gate.grade_design_review(write_review(path, payload), draft_path=path).result == "PASS"


@requires_validator
@pytest.mark.parametrize("asset", [None, "missing"])
def test_unbound_review_awaits_human(tmp_path, asset):
    """asset_sha256 null·키 누락은 어느 원고의 판정인지 대조할 수 없다 — 계약 §6 기본 권고 사람 판단."""
    path = draft(tmp_path)
    payload = review([finding(priority="P2")], asset_sha256=None)
    if asset == "missing":
        del payload["asset_sha256"]
    grade = gate.grade_design_review(write_review(path, payload), draft_path=path)
    assert grade.result == "AWAIT_HUMAN"
    assert "결속" in grade.detail or "asset_sha256" in grade.detail


@requires_validator
def test_hash_basis_is_draft_html_file_bytes(tmp_path):
    path = draft(tmp_path)
    target = write_review(path, review(asset_sha256=sha(path)))
    assert gate.grade_design_review(target, draft_path=path).result == "PASS"
    path.write_text(path.read_text() + "\n")  # 파싱 결과는 같아도 바이트가 바뀌면 다른 원고다.
    assert gate.grade_design_review(target, draft_path=path).result == "AWAIT_HUMAN"


@requires_validator
def test_open_p1_rule_defect_blocks(tmp_path):
    path = draft(tmp_path)
    target = write_review(path, review([finding(id="B-7", priority="P1", check="rule")]))
    grade = gate.grade_design_review(target, draft_path=path)
    assert grade.result == "BLOCK"
    assert "B-7" in grade.detail


@requires_validator
def test_open_p1_advisory_defect_awaits_human(tmp_path):
    path = draft(tmp_path)
    target = write_review(path, review([finding(id="B-3", priority="P1", check="advisory")]))
    grade = gate.grade_design_review(target, draft_path=path)
    assert grade.result == "AWAIT_HUMAN"
    assert "B-3" in grade.detail


@requires_validator
def test_rule_block_wins_over_advisory(tmp_path):
    path = draft(tmp_path)
    target = write_review(path, review([finding(id="B-1", priority="P1", check="advisory"),
                                        finding(id="B-2", priority="P1", check="rule")]))
    grade = gate.grade_design_review(target, draft_path=path)
    assert grade.result == "BLOCK"
    assert "B-1" in grade.detail and "B-2" in grade.detail


@requires_validator
@pytest.mark.parametrize("reason", ["missing", "", "   ", None, 3])
@pytest.mark.parametrize("check,expected", [("rule", "BLOCK"), ("advisory", "AWAIT_HUMAN")])
def test_waived_without_reason_counts_as_open(tmp_path, reason, check, expected):
    path = draft(tmp_path)
    item = finding(priority="P1", check=check, status="waived")
    if reason != "missing":
        item["waiver_reason"] = reason
    grade = gate.grade_design_review(write_review(path, review([item])), draft_path=path)
    assert grade.result == expected


@requires_validator
def test_waived_with_reason_fixed_and_lower_priorities_pass_with_counts(tmp_path):
    path = draft(tmp_path)
    findings = [
        finding(id="B-1", priority="P1", check="rule", status="waived", waiver_reason="사람 승인: 표 의도"),
        finding(id="B-2", priority="P1", check="rule", status="fixed"),
        finding(id="B-3", priority="P2", check="rule"),
        finding(id="B-4", priority="P3", kind="suggestion", check="advisory"),
        finding(id="B-5", priority="P2", kind="suggestion", check="advisory"),
    ]
    grade = gate.grade_design_review(write_review(path, review(findings)), draft_path=path)
    assert grade.result == "PASS"
    assert "\n" not in grade.detail
    for part in ("findings 5건", "fixed 1", "waived 1", "suggestion 2"):
        assert part in grade.detail


@requires_validator
def test_empty_findings_pass(tmp_path):
    path = draft(tmp_path)
    grade = gate.grade_design_review(write_review(path, review([])), draft_path=path)
    assert grade.result == "PASS"
    assert "findings 0건" in grade.detail


def test_detail_is_one_line_even_for_hostile_ids(tmp_path):
    path = draft(tmp_path)
    target = write_review(path, review([finding(id="B-1\nERR:x <b>", priority="P1", check="rule")]))
    grade = gate.grade_design_review(target, draft_path=path)
    assert grade.result == "AWAIT_HUMAN"  # 계약 밖 id 는 깨진 리뷰 — 정본 검사기가 사람 판단으로 넘긴다
    assert "\n" not in grade.detail


# ── 정본 검사기(validate_review.judge) 의미: waive 승인자·waiver_ref·부분 리뷰·계약 위반 ──────────

@requires_validator
def test_agent_waiver_of_p1_rule_still_blocks(tmp_path):
    path = draft(tmp_path)
    item = finding(id="B-4", priority="P1", status="waived", waiver_reason="스킨 소관", waived_by="codex:exec")
    grade = gate.grade_design_review(write_review(path, review([item])), draft_path=path)
    assert grade.result == "BLOCK"
    assert "B-4" in grade.detail


@requires_validator
def test_waiver_ref_without_resolvable_baseline_still_blocks(tmp_path):
    path = draft(tmp_path)
    item = finding(id="B-2", priority="P1", status="waived", waiver_reason="승계", waived_by="사용자",
                   waiver_ref=f"{tmp_path}/missing.design_review.json#B-8")
    grade = gate.grade_design_review(write_review(path, review([item])), draft_path=path)
    assert grade.result == "BLOCK"
    assert "B-2" in grade.detail


@requires_validator
def test_waiver_ref_to_human_baseline_passes(tmp_path):
    path = draft(tmp_path)
    approved = finding(id="B-8", priority="P1", status="waived", waiver_reason="플랫폼 위젯이라 원고에서 못 고친다",
                       waived_by="사용자", waived_at="2026-10-10T12:25:00+09:00")
    baseline = tmp_path / "floker-114.design_review.json"
    baseline.write_text(json.dumps(review([approved], asset_sha256="0" * 64)), encoding="utf-8")
    inherited = dict(approved, id="B-1", waiver_ref=f"{baseline}#B-8")
    grade = gate.grade_design_review(write_review(path, review([inherited])), draft_path=path)
    assert grade.result == "PASS"
    assert "waived 1" in grade.detail


@requires_validator
def test_partial_review_passes_and_says_so(tmp_path):
    path = draft(tmp_path)
    payload = review([finding(priority="P2")])
    payload["coverage"] = {**payload["coverage"], "complete": False,
                           "not_inspected": ["해석(advisory) 전부 — Codex rc=1"]}
    grade = gate.grade_design_review(write_review(path, payload), draft_path=path)
    assert grade.result == "PASS"
    assert "부분 리뷰" in grade.detail and "\n" not in grade.detail


@requires_validator
def test_contract_violation_outside_gate_fields_awaits_human(tmp_path):
    path = draft(tmp_path)
    payload = review([finding(priority="P2")])
    del payload["coverage"]
    grade = gate.grade_design_review(write_review(path, payload), draft_path=path)
    assert grade.result == "AWAIT_HUMAN"
    assert "E-COVERAGE" in grade.detail


def test_unavailable_validator_awaits_human(tmp_path, monkeypatch):
    path = draft(tmp_path)
    monkeypatch.setattr(gate, "VALIDATOR_PATH", tmp_path / "missing_validate_review.py")
    grade = gate.grade_design_review(write_review(path, review([])), draft_path=path)
    assert grade.result == "AWAIT_HUMAN"
    assert "검사기" in grade.detail


# ── 퍼블리셔 결선: 브라우저 실행 전, 마커 없음, 종료코드, 알림 ────────────────────

def test_exit_codes_for_design_holds_are_distinct_and_not_crash():
    assert pub.result_exit_code("DESIGN_BLOCK:x.html — open P1 ERR: in id", "publish") == 5
    assert pub.result_exit_code("DESIGN_AWAIT_HUMAN:x.html — 오래된 design review", "publish") == 6
    assert {pub.DESIGN_BLOCK_EXIT, pub.DESIGN_AWAIT_EXIT}.isdisjoint({0, 1, 3, 4})


@requires_validator
def test_gate_runs_before_browser_launch(isolated, monkeypatch):
    root, browser = isolated
    path = draft(root)
    write_review(path, review([finding(priority="P2")]))
    order = []
    real_grade = pub.grade_design_review

    def graded(*args, **kwargs):
        order.append("gate")
        return real_grade(*args, **kwargs)
    monkeypatch.setattr(pub, "grade_design_review", graded)
    browser.side_effect = lambda: (order.append("browser"), (_ for _ in ()).throw(BrowserBoundary()))[1]
    with pytest.raises(BrowserBoundary):
        pub.publish(str(root), "publish", DAY)
    assert order == ["gate", "browser"]


@requires_validator
@pytest.mark.parametrize("kind", ["daily", "periodic"])
@pytest.mark.parametrize("explicit", [True, False])
def test_block_leaves_no_success_marker_and_never_launches_browser(isolated, kind, explicit):
    root, browser = isolated
    path = draft(root, kind=kind)
    write_review(path, review([finding(priority="P1", check="rule")], asset_sha256=sha(path)))
    result = pub.publish(str(root), "publish", DAY if explicit else None, kind=kind)
    assert result.startswith("DESIGN_BLOCK:")
    assert pub.result_exit_code(result, "publish") == 5
    browser.assert_not_called()
    published, attempted, _ = pub.marker_paths(str(root), kind)
    assert not Path(published).exists() and not Path(attempted).exists()
    assert read_delivery(str(root), DAY, kind) is None


def test_await_human_leaves_no_success_marker(isolated):
    root, browser = isolated
    path = draft(root)
    write_review(path, review([finding(priority="P1", check="advisory")]))
    result = pub.publish(str(root), "publish", DAY)
    assert result.startswith("DESIGN_AWAIT_HUMAN:")
    assert pub.result_exit_code(result, "publish") == 6
    browser.assert_not_called()
    assert not Path(pub.marker_paths(str(root))[0]).exists()


def test_absent_review_logs_one_line_and_reaches_browser(isolated, capsys):
    root, browser = isolated
    draft(root)
    before = tree_bytes(root)
    with pytest.raises(BrowserBoundary):
        pub.publish(str(root), "publish", DAY)
    browser.assert_called_once()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1 and "design review 미실행" in lines[0]
    after = {p: b for p, b in tree_bytes(root).items() if "profile" not in p.parts}
    assert after == before


@requires_validator
def test_pass_logs_one_count_summary_line(isolated, capsys):
    root, browser = isolated
    path = draft(root)
    write_review(path, review([finding(priority="P2"), finding(id="B-2", priority="P3")]))
    with pytest.raises(BrowserBoundary):
        pub.publish(str(root), "publish", DAY)
    browser.assert_called_once()
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1 and "findings 2건" in lines[0]


class _Keys:
    def press(self, key):
        pass


class _Locator:
    def locator(self, selector):
        return self

    def click(self):
        pass


class _Page:
    url = "https://floker.tistory.com/manage/newpost/"
    keyboard = _Keys()

    def bring_to_front(self):
        pass

    def goto(self, *args, **kwargs):
        pass

    def wait_for_selector(self, *args, **kwargs):
        pass

    def wait_for_timeout(self, *args):
        pass

    def fill(self, *args):
        pass

    def frame_locator(self, selector):
        return _Locator()

    def evaluate(self, script, *args):
        if args and isinstance(args[0], list):  # 카테고리 후보 선택 — 실제 스크립트처럼 고른 이름을 돌려준다(U13)
            return args[0][0]
        return 5 if "innerHTML.length" in script else True


class _Context:
    def __init__(self):
        self.pages = [_Page()]

    def grant_permissions(self, *args, **kwargs):
        pass

    def on(self, *args):
        pass

    def close(self):
        pass


def _scripted_browser():
    """에디터 붙여넣기 검증(paste_basic_empty)에서 멈추는 결정론 브라우저 — log 전체가 결과에 실린다."""
    driver = types.SimpleNamespace(chromium=types.SimpleNamespace(
        launch_persistent_context=lambda *args, **kwargs: _Context()))

    class _Session:
        def __enter__(self):
            return driver

        def __exit__(self, *exc):
            return False
    return lambda: _Session()


def test_absent_review_result_is_byte_identical_to_current_publish(isolated, monkeypatch):
    root, _ = isolated
    draft(root)
    for name in ("_load_state", "_refresh_kakao_session", "_save_state"):
        monkeypatch.setattr(pub, name, lambda *args: None)
    monkeypatch.setattr(pub, "save_session_state", lambda *args: True)
    monkeypatch.setattr(subprocess, "run", Mock(side_effect=OSError("clipboard")))
    sys.modules["playwright.sync_api"].sync_playwright = _scripted_browser()
    result = pub.publish(str(root), "publish", DAY)
    assert result == ("[2026-09-11-tistory-draft.html] ERR:paste_basic_empty(5) | "
                      "REMOTE_LISTING_NO_MATCH TITLE CAT PASTE_BASIC:5")


def test_already_published_day_skips_without_consulting_gate(isolated, monkeypatch):
    root, browser = isolated
    path = draft(root)
    write_review(path, review([finding(priority="P1", check="rule")]))
    write_delivery(str(root), DAY, "daily", state="PUBLISHED", data=pub._parse_helper(str(path)))
    monkeypatch.setattr(pub, "grade_design_review", Mock(side_effect=AssertionError("gate")))
    assert pub.publish(str(root), "publish", DAY).startswith("SKIP:already_published")
    browser.assert_not_called()


@requires_validator
def test_flag_overrides_default_review_location(isolated, tmp_path):
    root, browser = isolated
    path = draft(root)
    write_review(path, review([finding(priority="P1", check="rule")]))
    clean = write_review(path, review([]), path=tmp_path / "elsewhere.json")
    with pytest.raises(BrowserBoundary):
        pub.publish(str(root), "publish", DAY, design_review=str(clean))
    blocking = write_review(path, review([finding(priority="P1", check="rule")]), path=tmp_path / "b.json")
    (Path(str(path)[:-5] + ".design_review.json")).unlink()
    assert pub.publish(str(root), "publish", DAY, design_review=str(blocking)).startswith("DESIGN_BLOCK:")


@requires_validator
def test_cli_block_exits_five_notifies_reason_and_writes_no_marker(isolated, monkeypatch, telegram, capsys):
    root, browser = isolated
    path = draft(root)
    write_review(path, review([finding(id="B-9", priority="P1", check="rule")]))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "synthetic-token-value")
    monkeypatch.setattr(sys, "argv", ["publish", "--mode", "publish", "--pending", "--outroot", str(root)])
    assert pub.main() == 5
    browser.assert_not_called()
    pub._notify_failure.assert_not_called()
    telegram.assert_called_once()
    text = telegram.call_args.args[0]
    assert "디자인 리뷰" in text and "B-9" in text and "synthetic-token-value" not in text
    assert "synthetic-token-value" not in capsys.readouterr().out
    assert not Path(pub.marker_paths(str(root))[0]).exists()
    assert not Path(pub.marker_paths(str(root))[2]).exists()  # 실패 알림 nag-once 를 가리지 않는다.


def test_cli_await_human_exits_six_and_notifies(isolated, monkeypatch, telegram):
    root, browser = isolated
    path = draft(root, kind="periodic")
    write_review(path, review(domain="shorts"))
    monkeypatch.setattr(sys, "argv", ["publish", "--mode", "publish", "--kind", "periodic",
                                     "--outroot", str(root)])
    assert pub.main() == 6
    browser.assert_not_called()
    telegram.assert_called_once()
    assert "domain" in telegram.call_args.args[0]
    assert not Path(pub.marker_paths(str(root), "periodic")[0]).exists()


@requires_validator
def test_design_alert_is_once_per_reason_per_day(isolated, monkeypatch, telegram):
    root, _ = isolated
    path = draft(root)
    target = write_review(path, review([finding(id="B-1", priority="P1", check="rule")]))
    monkeypatch.setattr(sys, "argv", ["publish", "--mode", "publish", "--date", DAY, "--outroot", str(root)])
    assert pub.main() == 5
    assert pub.main() == 5
    assert telegram.call_count == 1
    write_review(path, review([finding(id="B-2", priority="P1", check="advisory")]))
    assert pub.main() == 6
    assert telegram.call_count == 2


@requires_validator
def test_cli_no_notify_suppresses_design_alert(isolated, monkeypatch, telegram):
    root, _ = isolated
    path = draft(root)
    write_review(path, review([finding(priority="P1", check="rule")]))
    monkeypatch.setattr(sys, "argv", ["publish", "--mode", "publish", "--date", DAY, "--no-notify",
                                     "--outroot", str(root)])
    assert pub.main() == 5
    telegram.assert_not_called()


def test_cli_design_review_flag_is_publish_only(isolated, monkeypatch):
    root, _ = isolated
    monkeypatch.setattr(sys, "argv", ["publish", "--mode", "inject", "--design-review", "x.json",
                                     "--outroot", str(root)])
    with pytest.raises(SystemExit) as stop:
        pub.main()
    assert stop.value.code == 2
