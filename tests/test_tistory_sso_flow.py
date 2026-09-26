"""SSO 선택 뒤 지연 전환·원래 에디터 복귀·검증 세션 격리를 확인한다."""
from unittest.mock import Mock

import pytest

from blog import tistory_publish_pw as pub
from blog.tistory_keychain import KakaoCredentials


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


def test_missing_keychain_still_requires_human_login(monkeypatch):
    page = LoginPage(["https://accounts.kakao.com/login/simple/"])
    choose = Mock(return_value="PW_FORM")
    monkeypatch.setattr(pub, "select_kakao_account", choose)
    monkeypatch.setattr(
        pub,
        "read_kakao_credentials",
        lambda log: log.append("KEYCHAIN_CREDENTIALS_MISSING"),
    )
    log = []
    assert not pub._relogin_via_kakao_sso(page, log)
    page.goto.assert_not_called()
    assert choose.call_count == 1
    assert log[-2:] == ["SSO_PW_FORM", "KEYCHAIN_CREDENTIALS_MISSING"]


def test_multiple_accounts_still_require_human_choice(monkeypatch):
    page = LoginPage(["https://accounts.kakao.com/login/simple/"])
    monkeypatch.setattr(pub, "select_kakao_account", Mock(return_value="ACCOUNT_SELECTION"))
    read = Mock(side_effect=AssertionError("Keychain must not choose an identity"))
    monkeypatch.setattr(pub, "read_kakao_credentials", read)
    log = []

    assert not pub._relogin_via_kakao_sso(page, log)
    assert log[-1] == "SSO_ACCOUNT_SELECTION_REQUIRED"
    read.assert_not_called()


def test_password_form_uses_keychain_once_and_completes_sso(monkeypatch):
    page = LoginPage([
        "https://accounts.kakao.com/login/",
        "https://floker.tistory.com/manage/",
    ])
    choose = Mock(return_value="PW_FORM")
    submit = Mock(return_value="SUBMITTED")
    monkeypatch.setattr(pub, "select_kakao_account", choose)
    monkeypatch.setattr(pub, "submit_kakao_credentials", submit)
    monkeypatch.setattr(
        pub,
        "read_kakao_credentials",
        Mock(return_value=KakaoCredentials("synthetic-login", "synthetic-password")),
    )
    log = []

    assert pub._relogin_via_kakao_sso(page, log)
    submit.assert_called_once_with(
        page, "synthetic-login", "synthetic-password", allow_password_only=False)
    assert "KEYCHAIN_LOGIN_SUBMITTED" in log
    assert log[-1] == "AUTO_RELOGIN_OK"
    assert "synthetic-login" not in " ".join(log)
    assert "synthetic-password" not in " ".join(log)


def test_password_only_permission_requires_saved_account_click_in_same_flow(monkeypatch):
    page = LoginPage([
        "https://accounts.kakao.com/login/simple/",
        "https://accounts.kakao.com/login/",
        "https://floker.tistory.com/manage/",
    ])
    choose = Mock(side_effect=["TILE_CLICKED", "PW_FORM"])
    submit = Mock(return_value="SUBMITTED")
    monkeypatch.setattr(pub, "select_kakao_account", choose)
    monkeypatch.setattr(pub, "submit_kakao_credentials", submit)
    monkeypatch.setattr(
        pub,
        "read_kakao_credentials",
        Mock(return_value=KakaoCredentials("synthetic-login", "synthetic-password")),
    )

    assert pub._relogin_via_kakao_sso(page, [])
    submit.assert_called_once_with(
        page, "synthetic-login", "synthetic-password", allow_password_only=True)


def test_rejected_keychain_login_is_not_retried_or_leaked(monkeypatch):
    page = LoginPage(["https://accounts.kakao.com/login/"] * 21)
    monkeypatch.setattr(pub, "select_kakao_account", Mock(return_value="PW_FORM"))
    credentials = Mock(return_value=KakaoCredentials("synthetic-login", "synthetic-password"))
    submit = Mock(return_value="SUBMITTED")
    monkeypatch.setattr(pub, "read_kakao_credentials", credentials)
    monkeypatch.setattr(pub, "submit_kakao_credentials", submit)
    log = []

    assert not pub._relogin_via_kakao_sso(page, log)
    credentials.assert_called_once()
    submit.assert_called_once()
    assert log[-1] == "KEYCHAIN_LOGIN_NOT_CONFIRMED"
    assert "synthetic-" not in " ".join(log)


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
