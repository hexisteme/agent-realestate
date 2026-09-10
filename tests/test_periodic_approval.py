"""첫 두 결산 검토 경계 — 합성 원고/합성 승인만, 브라우저·네트워크 실행 없음."""
import datetime
import html
import json
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

from blog import periodic_approval as review
from blog import tistory_publish_pw as publish


class BrowserBoundary(Exception):
    pass


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    class Today(datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 9, 13)
    monkeypatch.setattr(publish.datetime, "date", Today)
    monkeypatch.setattr(publish, "PROFILE_DIR", str(tmp_path / "fake-profile"))
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = Mock(side_effect=BrowserBoundary)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    monkeypatch.setattr(publish, "_verify_published_on_blog", Mock(side_effect=AssertionError("remote")))
    monkeypatch.setattr(publish, "_notify_failure", Mock(side_effect=AssertionError("notify")))
    for kind in ("daily", "periodic"):
        for p in publish.marker_paths(str(tmp_path), kind):
            Path(p).write_text("unchanged")
    return tmp_path, api.sync_playwright


def draft(root, date="2026-09-13", kind="periodic"):
    path = root / "report/blog/tistory" / f"{date}{'-periodic' if kind == 'periodic' else ''}-tistory-draft.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('<textarea id=t>합성 검토 제목</textarea><textarea id=g>태그</textarea>'
                    f'<textarea id=b>{html.escape("<p>합성 테스트 본문</p>")}</textarea>')
    return str(path)


def approve_fixture(root, date):
    args = ["--date", date, "--outroot", str(root)]
    assert review.main(["prepare", *args]) == 3
    pending = json.loads(review.review_path(str(root), date, "review.json").read_text())
    assert not review.review_path(str(root), date, "approval.json").exists()
    assert review.main(["approve", *args, "--digest", pending["digest"],
                        "--reviewer", "TEST HUMAN", "--note", "SYNTHETIC TEST ONLY"]) == 0


@pytest.mark.parametrize("date", review.FIRST_REVIEW_DATES)
@pytest.mark.parametrize("explicit,post_id", [(False, None), (True, None), (True, "79"), (False, "79")])
def test_await_before_browser_remote_or_markers(prepared, monkeypatch, date, explicit, post_id):
    root, browser = prepared
    monkeypatch.setattr(publish.datetime.date, "today", classmethod(lambda cls: cls.fromisoformat(date)))
    draft(root, date)
    attempted = Path(publish.marker_paths(str(root), "periodic")[1])
    attempted.write_text(date)  # 재시도 원격 대조/마커 복구보다도 먼저 중단
    before = {p: p.read_bytes() for p in root.glob(".last-*")}
    result = publish.publish(str(root), "publish", date if explicit else None, post_id=post_id, kind="periodic")
    assert result.startswith("AWAIT_REVIEW:")
    assert before == {p: p.read_bytes() for p in root.glob(".last-*")}
    browser.assert_not_called()
    assert not (root / "fake-profile").exists()


@pytest.mark.parametrize("field", ["title", "body", "tags", "wrapper", "date", "kind"])
def test_approval_bound_to_every_published_field_and_identity(prepared, field):
    root, _ = prepared
    path = draft(root)
    approve_fixture(root, "2026-09-13")
    assert not review.needs_review(str(root), path, publish._parse_helper(path), "periodic")
    if field in ("date", "kind"):
        receipt = review.review_path(str(root), "2026-09-13", "approval.json")
        data = json.loads(receipt.read_text()); data[field] = "changed"
        receipt.write_text(json.dumps(data))
    else:
        text = Path(path).read_text()
        replacements = {"title": ("제목", "수정"), "body": ("본문", "수정"), "tags": ("태그", "수정"),
                        "wrapper": ("<textarea", "\n<textarea")}
        Path(path).write_text(text.replace(*replacements[field]))
    assert review.needs_review(str(root), path, publish._parse_helper(path), "periodic")


@pytest.mark.parametrize("date,kind,approved", [("2026-09-13", "daily", False),
                         ("2026-09-20", "periodic", False), ("2026-09-13", "periodic", True)])
def test_unaffected_and_approved_reach_existing_browser_path(prepared, date, kind, approved):
    root, browser = prepared
    draft(root, date, kind)
    if approved:
        approve_fixture(root, date)
    # 승인 통과/비대상도 명시 날짜 발행의 중복 대조는 수행한다.
    publish._verify_published_on_blog.side_effect = None
    publish._verify_published_on_blog.return_value = False
    with pytest.raises(BrowserBoundary):
        publish.publish(str(root), "publish", date, kind=kind)
    browser.assert_called_once()


def test_cli_wait_is_exit_three_without_failure_alert(prepared, monkeypatch):
    root, browser = prepared
    draft(root)
    monkeypatch.setattr(sys, "argv", ["publish", "--mode", "publish", "--date", "2026-09-13",
                                     "--kind", "periodic", "--post-id", "79", "--outroot", str(root)])
    assert publish.main() == 3
    browser.assert_not_called()
    publish._notify_failure.assert_not_called()


def test_approve_rejects_changed_draft_after_prepare(prepared):
    root, _ = prepared
    path = draft(root)
    args = ["--date", "2026-09-13", "--outroot", str(root)]
    review.main(["prepare", *args])
    pending = json.loads(review.review_path(str(root), "2026-09-13", "review.json").read_text())
    Path(path).write_text(Path(path).read_text() + "changed")
    with pytest.raises(SystemExit, match="2"):
        review.main(["approve", *args, "--digest", pending["digest"], "--reviewer", "TEST", "--note", "TEST"])
    assert not review.review_path(str(root), "2026-09-13", "approval.json").exists()
