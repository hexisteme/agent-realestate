"""Keychain 경계가 비밀값을 argv·로그로 내보내지 않는지 확인한다."""

import subprocess
from unittest.mock import Mock

from blog import tistory_keychain as keychain


def pair_marker(tmp_path, monkeypatch):
    marker = tmp_path / ".tistory-keychain-pair-v1"
    marker.write_text("v1\n", encoding="ascii")
    marker.chmod(0o600)
    monkeypatch.setattr(keychain, "PAIR_MARKER", str(marker))


def profile_lock(tmp_path, monkeypatch):
    lock = tmp_path / "profile/.lock"
    monkeypatch.setattr(keychain, "PROFILE_LOCK", str(lock))
    return lock


def test_read_uses_fixed_commands_and_redacts_representation(tmp_path, monkeypatch):
    pair_marker(tmp_path, monkeypatch)
    secrets = {
        keychain.LOGIN_SERVICE: b"synthetic-login\n",
        keychain.PASSWORD_SERVICE: b"synthetic-password\n",
    }
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, stdout=secrets[args[-2]])

    monkeypatch.setattr(keychain.subprocess, "run", run)
    log = []
    credentials = keychain.read_kakao_credentials(log)

    assert credentials is not None
    assert credentials.login_id == "synthetic-login"
    assert credentials.password == "synthetic-password"
    assert repr(credentials) == "KakaoCredentials(<redacted>)"
    assert log == ["KEYCHAIN_CREDENTIALS_SET"]
    flattened = " ".join(arg for args, _ in calls for arg in args)
    assert "synthetic-login" not in flattened
    assert "synthetic-password" not in flattened
    assert all(kwargs["stderr"] is subprocess.DEVNULL for _, kwargs in calls)
    assert all(kwargs["check"] is False and kwargs["timeout"] == 8 for _, kwargs in calls)


def test_missing_or_failed_lookup_has_one_bit_diagnostic(tmp_path, monkeypatch):
    pair_marker(tmp_path, monkeypatch)
    failure = subprocess.CompletedProcess([], 44, stdout=b"")
    run = Mock(return_value=failure)
    monkeypatch.setattr(keychain.subprocess, "run", run)
    log = []

    assert keychain.read_kakao_credentials(log) is None
    assert log == ["KEYCHAIN_CREDENTIALS_MISSING"]
    assert run.call_count == 1


def test_lookup_exception_payload_is_never_logged(tmp_path, monkeypatch):
    pair_marker(tmp_path, monkeypatch)
    monkeypatch.setattr(
        keychain.subprocess,
        "run",
        Mock(side_effect=subprocess.TimeoutExpired("synthetic-secret", 8)),
    )
    log = []

    assert keychain.read_kakao_credentials(log) is None
    assert log == ["KEYCHAIN_CREDENTIALS_MISSING"]
    assert "synthetic-secret" not in " ".join(log)


def test_setup_delegates_hidden_prompt_without_secret_argument(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(keychain.subprocess, "run", run)

    assert keychain._prompt_store(keychain.PASSWORD_SERVICE)
    args = run.call_args.args[0]
    assert args == [
        "/usr/bin/security", "add-generic-password", "-a", "publisher",
        "-s", keychain.PASSWORD_SERVICE, "-w",
    ]
    assert run.call_args.kwargs == {"check": False}


def test_setup_refuses_noninteractive_input(tmp_path, monkeypatch, capsys):
    profile_lock(tmp_path, monkeypatch)
    monkeypatch.setattr(keychain.sys.stdin, "isatty", Mock(return_value=False))
    run = Mock()
    monkeypatch.setattr(keychain.subprocess, "run", run)

    assert keychain.setup_keychain_credentials() == 2
    assert capsys.readouterr().out.strip() == "ERR:interactive_terminal_required"
    run.assert_not_called()


def test_uncommitted_pair_is_never_read_from_keychain(tmp_path, monkeypatch):
    monkeypatch.setattr(keychain, "PAIR_MARKER", str(tmp_path / "missing-marker"))
    run = Mock(side_effect=AssertionError("uncommitted pair must not be read"))
    monkeypatch.setattr(keychain.subprocess, "run", run)
    log = []

    assert keychain.read_kakao_credentials(log) is None
    assert log == ["KEYCHAIN_CREDENTIALS_MISSING"]
    run.assert_not_called()


def test_pair_marker_is_atomic_and_permission_restricted(tmp_path, monkeypatch):
    marker = tmp_path / "profile/.tistory-keychain-pair-v1"
    monkeypatch.setattr(keychain, "PAIR_MARKER", str(marker))

    assert keychain._write_pair_marker()
    assert marker.read_text(encoding="ascii") == "v1\n"
    assert marker.stat().st_mode & 0o777 == 0o600
    assert keychain._pair_marker_ready()


def test_clear_treats_missing_as_success_and_attempts_both_items(tmp_path, monkeypatch):
    marker = tmp_path / "pair-marker"
    marker.write_text("v1\n")
    monkeypatch.setattr(keychain, "PAIR_MARKER", str(marker))
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], keychain.ITEM_NOT_FOUND),
        subprocess.CompletedProcess([], 0),
    ])
    monkeypatch.setattr(keychain.subprocess, "run", run)

    assert keychain._clear_keychain_credentials()
    assert run.call_count == 2
    assert all(call.args[0][1] == "delete-generic-password" for call in run.call_args_list)
    assert all(call.kwargs["stderr"] is subprocess.DEVNULL for call in run.call_args_list)
    assert not marker.exists()


def test_setup_partial_write_clears_pair_and_returns_failure(tmp_path, monkeypatch, capsys):
    profile_lock(tmp_path, monkeypatch)
    monkeypatch.setattr(keychain.sys.stdin, "isatty", Mock(return_value=True))
    clear = Mock(return_value=True)
    store = Mock(side_effect=[True, False])
    monkeypatch.setattr(keychain, "_clear_keychain_credentials", clear)
    monkeypatch.setattr(keychain, "_prompt_store", store)

    assert keychain.setup_keychain_credentials() == 1
    assert clear.call_count == 2
    assert store.call_args_list == [
        ((keychain.LOGIN_SERVICE,),),
        ((keychain.PASSWORD_SERVICE,),),
    ]
    assert capsys.readouterr().out.rstrip().endswith("ERR:keychain_setup_failed(password)")


def test_setup_commit_failure_clears_both_items(tmp_path, monkeypatch, capsys):
    profile_lock(tmp_path, monkeypatch)
    monkeypatch.setattr(keychain.sys.stdin, "isatty", Mock(return_value=True))
    clear = Mock(return_value=True)
    monkeypatch.setattr(keychain, "_clear_keychain_credentials", clear)
    monkeypatch.setattr(keychain, "_prompt_store", Mock(return_value=True))
    monkeypatch.setattr(
        keychain,
        "_read_keychain_pair",
        Mock(return_value=keychain.KakaoCredentials("synthetic-login", "synthetic-password")),
    )
    monkeypatch.setattr(keychain, "_write_pair_marker", Mock(return_value=False))

    assert keychain.setup_keychain_credentials() == 1
    assert clear.call_count == 2
    assert capsys.readouterr().out.rstrip().endswith("ERR:keychain_commit_failed")


def test_setup_stops_before_secret_prompts_when_publisher_lock_is_held(
    tmp_path, monkeypatch, capsys,
):
    profile_lock(tmp_path, monkeypatch)
    monkeypatch.setattr(keychain.sys.stdin, "isatty", Mock(return_value=True))
    monkeypatch.setattr(
        keychain.fcntl,
        "flock",
        Mock(side_effect=BlockingIOError("synthetic-private-lock-detail")),
    )
    clear = Mock(side_effect=AssertionError("must stop before Keychain mutation"))
    monkeypatch.setattr(keychain, "_clear_keychain_credentials", clear)

    assert keychain.setup_keychain_credentials() == 4
    assert capsys.readouterr().out.strip() == "ERR:profile_locked"
    clear.assert_not_called()
