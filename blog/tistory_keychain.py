"""macOS Keychain boundary for the Tistory publisher's Kakao login.

Credentials are never accepted through argv or environment variables.  Setup delegates
the secret prompt to ``security`` by placing ``-w`` last, and reads return only through a
captured pipe in the publishing process.  Callers may log the fixed status strings this
module returns; they must never log the credential object.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass


SECURITY = "/usr/bin/security"
KEYCHAIN_ACCOUNT = "publisher"
LOGIN_SERVICE = "agent-realestate.tistory-kakao.login"
PASSWORD_SERVICE = "agent-realestate.tistory-kakao.password"
ITEM_NOT_FOUND = 44
PROFILE_DIR = os.environ.get(
    "TISTORY_PW_PROFILE", "/Volumes/EXT_SSD/bot/agent_realestate/.pw-profile",
)
PAIR_MARKER = os.path.join(PROFILE_DIR, ".tistory-keychain-pair-v1")
PROFILE_LOCK = os.path.join(PROFILE_DIR, ".lock")


@dataclass(frozen=True, repr=False)
class KakaoCredentials:
    login_id: str
    password: str

    def __repr__(self) -> str:
        return "KakaoCredentials(<redacted>)"


def _read_keychain_secret(service: str) -> str | None:
    """Read one fixed-service item without exposing stderr or exception payloads."""
    try:
        completed = subprocess.run(
            [SECURITY, "find-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", service, "-w"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout:
        return None
    try:
        value = completed.stdout.decode("utf-8").rstrip("\r\n")
    except UnicodeError:
        return None
    return value or None


def _read_keychain_pair() -> KakaoCredentials | None:
    login_id = _read_keychain_secret(LOGIN_SERVICE)
    password = _read_keychain_secret(PASSWORD_SERVICE) if login_id is not None else None
    if login_id is None or password is None:
        return None
    return KakaoCredentials(login_id=login_id, password=password)


def _pair_marker_ready() -> bool:
    try:
        if stat.S_IMODE(os.stat(PAIR_MARKER).st_mode) != 0o600:
            return False
        with open(PAIR_MARKER, encoding="ascii") as marker:
            return marker.read() == "v1\n"
    except OSError:
        return False


def _write_pair_marker() -> bool:
    try:
        os.makedirs(os.path.dirname(PAIR_MARKER), exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            dir=os.path.dirname(PAIR_MARKER), prefix=".tistory-keychain-pair-",
        )
        try:
            os.fchmod(fd, 0o600)
            os.write(fd, b"v1\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, PAIR_MARKER)
        return True
    except OSError:
        try:
            os.unlink(temporary)
        except (OSError, UnboundLocalError):
            pass
        return False


def _remove_pair_marker() -> None:
    try:
        os.unlink(PAIR_MARKER)
    except FileNotFoundError:
        pass


def read_kakao_credentials(log: list[str]) -> KakaoCredentials | None:
    """Return a complete, setup-committed pair with one-bit diagnostics only."""
    credentials = _read_keychain_pair() if _pair_marker_ready() else None
    if credentials is None:
        log.append("KEYCHAIN_CREDENTIALS_MISSING")
        return None
    log.append("KEYCHAIN_CREDENTIALS_SET")
    return credentials


def _prompt_store(service: str) -> bool:
    """Ask ``security`` to read a value from the TTY; no secret crosses argv/stdin here."""
    try:
        completed = subprocess.run(
            [SECURITY, "add-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", service, "-w"],
            check=False,
        )
    except OSError:
        return False
    return completed.returncode == 0


def _delete_keychain_item(service: str) -> bool:
    """Delete one item; absence is already the desired state."""
    try:
        completed = subprocess.run(
            [SECURITY, "delete-generic-password", "-a", KEYCHAIN_ACCOUNT,
             "-s", service],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode in {0, ITEM_NOT_FOUND}


def _clear_keychain_credentials() -> bool:
    _remove_pair_marker()
    results = [_delete_keychain_item(LOGIN_SERVICE), _delete_keychain_item(PASSWORD_SERVICE)]
    return all(results)


def _setup_keychain_credentials_locked() -> int:
    if not _clear_keychain_credentials():
        print("ERR:keychain_reset_failed")
        return 1
    print("Kakao login ID를 Keychain에 등록합니다. 다음 password 프롬프트에 ID를 입력하세요.")
    if not _prompt_store(LOGIN_SERVICE):
        _clear_keychain_credentials()
        print("ERR:keychain_setup_failed(login)")
        return 1
    print("Kakao password를 Keychain에 등록합니다. 다음 password 프롬프트에 비밀번호를 입력하세요.")
    if not _prompt_store(PASSWORD_SERVICE):
        _clear_keychain_credentials()
        print("ERR:keychain_setup_failed(password)")
        return 1
    credentials = _read_keychain_pair()
    if credentials is None:
        _clear_keychain_credentials()
        print("MISSING")
        return 1
    del credentials
    if not _write_pair_marker():
        _clear_keychain_credentials()
        print("ERR:keychain_commit_failed")
        return 1
    print("SET")
    return 0


def setup_keychain_credentials() -> int:
    """Replace the pair while holding the publisher's profile lock."""
    if not sys.stdin.isatty():
        print("ERR:interactive_terminal_required")
        return 2
    try:
        os.makedirs(os.path.dirname(PROFILE_LOCK), exist_ok=True)
        with open(PROFILE_LOCK, "a", encoding="utf-8") as lock_file:
            try:
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("ERR:profile_locked")
                return 4
            return _setup_keychain_credentials_locked()
    except OSError:
        print("ERR:profile_lock_unavailable")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Tistory Kakao Keychain 자격증명 설정/확인")
    parser.add_argument("command", choices=["setup", "status"])
    args = parser.parse_args()
    if args.command == "setup":
        return setup_keychain_credentials()
    probe_log: list[str] = []
    credentials = read_kakao_credentials(probe_log)
    if credentials is None:
        print("MISSING")
        return 1
    del credentials
    print("SET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
