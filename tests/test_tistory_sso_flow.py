"""SSO 선택 뒤 지연 전환·원래 에디터 복귀·검증 세션 격리를 확인한다."""
from unittest.mock import Mock

import pytest

from blog import tistory_publish_pw as pub


class LoginPage:
    def __init__(self, transitions):
        self.url = "https://www.tistory.com/auth/login"
        self.transitions = iter(transitions)
        self.evaluate = Mock(return_value=True)
        self.goto = Mock(side_effect=self.navigate)
        self.wait_for_selector = Mock()

    def navigate(self, url, **kwargs):
        self.url = url

    def wait_for_timeout(self, _):
        self.url = next(self.transitions, self.url)


def test_delayed_sso_clicks_account_once_and_returns_to_original_edit(monkeypatch):
    page = LoginPage(["https://accounts.kakao.com/login/simple/"] * 3
                     + ["https://floker.tistory.com/manage/"])
    calls = []
    def choose(page, *, already_clicked):
        calls.append(already_clicked)
        return "WAIT" if already_clicked else "TILE_CLICKED"
    monkeypatch.setattr(pub, "select_kakao_account", choose)
    log = []
    target = "https://floker.tistory.com/manage/newpost/86/"
    assert pub._relogin_via_kakao_sso(page, log, target)
    assert calls == [False, True, True]
    assert log.count("SSO_ACCOUNT_CLICKED") == 1
    assert page.goto.call_args.args == (target,)
    assert log[-1] == "AUTO_RELOGIN_OK"


@pytest.mark.parametrize("state, expected", [
    ("PW_FORM", "SSO_PW_FORM"),
    ("ACCOUNT_SELECTION", "SSO_ACCOUNT_SELECTION_REQUIRED"),
])
def test_real_human_login_requirements_stop_auto_clicks(monkeypatch, state, expected):
    page = LoginPage(["https://accounts.kakao.com/login/simple/"])
    choose = Mock(return_value=state)
    monkeypatch.setattr(pub, "select_kakao_account", choose)
    log = []
    assert not pub._relogin_via_kakao_sso(page, log)
    page.goto.assert_not_called()
    assert choose.call_count == 1 and log[-1] == expected


def test_unknown_layout_is_distinct_and_does_not_log_exception_payload(monkeypatch):
    page = LoginPage(["https://accounts.kakao.com/login/simple/"])
    monkeypatch.setattr(pub, "select_kakao_account", Mock(return_value="LAYOUT_UNRECOGNIZED"))
    page.wait_for_selector.side_effect = RuntimeError("synthetic-private-value")
    log = []
    assert not pub._relogin_via_kakao_sso(page, log)
    assert "SSO_LAYOUT_UNRECOGNIZED" in log
    assert "synthetic-private-value" not in " ".join(log)


@pytest.mark.parametrize("outcome", [True, False])
def test_expiry_probe_uses_only_kakao_in_separate_memory_context(monkeypatch, outcome):
    ctx = Mock()
    cookies = [{"domain": ".kakao.com", "value": "synthetic-kakao"},
               {"domain": "accounts.kakao.com", "value": "synthetic-account"},
               {"domain": ".tistory.com", "value": "synthetic-tistory"},
               {"domain": "notkakao.com", "value": "synthetic-other"}]
    ctx.cookies.return_value = cookies
    isolated = ctx.browser.new_context.return_value
    monkeypatch.setattr(pub, "_relogin_via_kakao_sso", Mock(return_value=outcome))
    log = []
    assert pub._probe_kakao_relogin(ctx, pub.NEWPOST_URL, log) is outcome
    isolated.add_cookies.assert_called_once_with(cookies[:2])
    isolated.close.assert_called_once()
    ctx.clear_cookies.assert_not_called()
    ctx.add_cookies.assert_not_called()
    assert "synthetic-" not in " ".join(log)


def test_expiry_probe_closes_isolation_on_navigation_failure():
    ctx = Mock()
    ctx.cookies.return_value = []
    isolated = ctx.browser.new_context.return_value
    isolated.new_page.return_value.goto.side_effect = TimeoutError()
    with pytest.raises(TimeoutError):
        pub._probe_kakao_relogin(ctx, pub.NEWPOST_URL, [])
    isolated.close.assert_called_once()


def test_probe_cannot_enter_publication_path():
    assert pub.publish(mode="publish", probe_relogin=True) == "ERR:relogin_probe_requires_auth_mode"
