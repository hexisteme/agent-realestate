"""인증 실패·날짜별 복구·불명확 발행 상태를 외부 부작용 없이 재현한다."""
import datetime
import html
import sys
import types
from unittest.mock import Mock

import pytest

from blog import tistory_publish_pw as pub
from blog.tistory_delivery import read_delivery, write_delivery

DATA = {"title": "합성 일간 — 2026-09-10", "body": "<p>합성 본문</p>", "tags": "합성"}


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
    return tmp_path


def draft(root, day):
    p = root / f"report/blog/tistory/{day}-tistory-draft.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(''.join(f'<textarea id={k}>{html.escape(v)}</textarea>'
                         for k, v in zip(('t', 'b', 'g'), DATA.values())))
    return p


@pytest.mark.parametrize('result,mode,rc', [
    ('ERR:login_timeout', 'publish', 1),
    ('[draft] ERR:paste_content_mismatch', 'publish', 1),
    ('SKIP:verified_published_remote | MARKER_FAIL', 'publish', 1),
    ('[draft] PUBLISHED STATE_SAVE_FAIL:OSError', 'publish', 1),
    ('[draft] NO_REDIRECT', 'publish', 1),
    ('[draft] NOT_PUBLISHED', 'publish', 1),
    ('[draft] PUBLISHED(remote-verified)', 'publish', 0),
    ('SKIP:already_published(2026-09-10,daily)', 'publish', 0),
    ('SKIP:profile_locked', 'publish', 4),
    ('AWAIT_REVIEW:2026-09-13', 'publish', 3),
    ('AUTH_OK | STATE_SAVED', 'auth', 0),
    ('[draft] INJECT_OK', 'inject', 0),
])
def test_outcomes_have_explicit_status(result, mode, rc):
    assert pub.result_exit_code(result, mode) == rc


def test_cli_failure_propagates_and_redacts_exception(isolated, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['publish', '--mode', 'publish', '--outroot', str(isolated)])
    monkeypatch.setattr(pub, 'publish', Mock(side_effect=RuntimeError('synthetic-token-query')))
    assert pub.main() == 1
    out = capsys.readouterr().out
    assert 'RuntimeError' in out and 'synthetic-token-query' not in out
    pub._notify_failure.assert_called_once()


def test_cli_no_notify_and_pending_stop_on_first_failure(isolated, monkeypatch):
    for day in ('2026-09-10', '2026-09-11'):
        draft(isolated, day)
    (isolated / pub.PUBLISH_MARKER).write_text('2026-09-09')
    monkeypatch.setattr(sys, 'argv', ['publish', '--mode', 'publish', '--pending', '--no-notify',
                                     '--outroot', str(isolated)])
    call = Mock(side_effect=['ERR:login_timeout', '[draft] PUBLISHED'])
    monkeypatch.setattr(pub, 'publish', call)
    assert pub.main() == 1
    assert call.call_count == 1 and call.call_args.args[2] == '2026-09-10'
    pub._notify_failure.assert_not_called()


def test_pending_recovery_oldest_first_bounded_and_no_future(isolated):
    for d in range(8, 14):
        draft(isolated, f'2026-09-{d:02d}')
    marker = isolated / pub.PUBLISH_MARKER
    marker.write_text('2026-09-08')
    assert pub.pending_dates(str(isolated), '2026-09-11') == ['2026-09-09', '2026-09-10']
    marker.write_text('2026-09-10')
    assert pub.pending_dates(str(isolated), '2026-09-11') == ['2026-09-11']
    marker.unlink()
    assert pub.pending_dates(str(isolated), '2026-09-11') == ['2026-09-11']


def test_old_day_receipt_prevents_duplicate_and_does_not_regress_marker(isolated):
    marker = isolated / pub.PUBLISH_MARKER
    marker.write_text('2026-09-11')
    pub._record_published(str(isolated), '2026-09-10', 'daily', DATA)
    assert marker.read_text() == '2026-09-11'
    assert pub._check_delivery(str(isolated), '2026-09-10', 'daily', DATA,
                               explicit=True, log=[]).startswith('SKIP:already_published')
    pub._verify_published_on_blog.assert_not_called()
    assert read_delivery(str(isolated), '2026-09-10', 'daily')['state'] == 'PUBLISHED'


@pytest.mark.parametrize('remote', [None, False])
def test_unknown_remote_cannot_reenter_click_path(isolated, monkeypatch, remote):
    path = draft(isolated, '2026-09-10')
    data = pub._parse_helper(str(path))
    write_delivery(str(isolated), '2026-09-10', 'daily', state='ATTEMPTED', data=data)
    pub._verify_published_on_blog.return_value = remote
    browser = Mock(side_effect=AssertionError('browser must not start'))
    package = types.ModuleType('playwright')
    api = types.ModuleType('playwright.sync_api')
    api.sync_playwright = browser
    package.sync_api = api
    monkeypatch.setitem(sys.modules, 'playwright', package)
    monkeypatch.setitem(sys.modules, 'playwright.sync_api', api)
    assert pub.publish(str(isolated), 'publish', '2026-09-10').startswith('ERR:publication_unknown')
    browser.assert_not_called()
    assert read_delivery(str(isolated), '2026-09-10', 'daily')['state'] == 'ATTEMPTED'


def test_receipt_rechecked_after_lock_acquisition(isolated, monkeypatch):
    path = draft(isolated, '2026-09-10')
    import fcntl
    original = fcntl.flock
    def competing_commit(fd, flags):
        pub._record_published(str(isolated), '2026-09-10', 'daily', pub._parse_helper(str(path)))
        return original(fd, flags)
    monkeypatch.setattr(fcntl, 'flock', competing_commit)
    assert pub.publish(str(isolated), 'publish', '2026-09-10').startswith('SKIP:already_published')
    pub._verify_published_on_blog.assert_not_called()


def test_attempt_payload_change_requires_reconciliation(isolated):
    write_delivery(str(isolated), '2026-09-10', 'daily', state='ATTEMPTED', data=DATA)
    result = pub._check_delivery(str(isolated), '2026-09-10', 'daily',
                                {**DATA, 'body': 'changed'}, explicit=True, log=[])
    assert result.startswith('ERR:draft_changed_after_attempt')
    pub._verify_published_on_blog.assert_not_called()


def test_remote_network_error_is_not_absence(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', Mock(side_effect=OSError('synthetic-secret')))
    log = []
    assert pub._verify_published_on_blog('synthetic', log) is None
    assert log == ['REMOTE_VERIFY_FAIL:OSError']


def test_auth_stage_never_returns_queries():
    assert pub._auth_stage('https://accounts.kakao.com/login?code=synthetic-secret') == 'KAKAO_LOGIN'
    assert pub._auth_stage('https://floker.tistory.com.evil.invalid/auth/login') == 'OTHER_PAGE'


def test_notification_exception_never_logs_raw_secret(tmp_path, monkeypatch, capsys):
    from agent_realestate import config
    monkeypatch.setattr(config, 'load_env_file', Mock(side_effect=OSError('synthetic-secret')))
    pub._notify_failure('ERR:login_timeout', str(tmp_path))
    out = capsys.readouterr().out
    assert 'OSError' in out and 'synthetic-secret' not in out
