"""Synthetic cookie-only regression tests; never open a browser profile."""

import json
import stat

import pytest

from blog import tistory_session as session


def cookie(name="session", value="synthetic", domain=".tistory.com", **extra):
    return {"name": name, "value": value, "domain": domain, "path": "/",
            "expires": -1, "secure": True, "httpOnly": True, **extra}


class Context:
    def __init__(self, current=(), saved=None):
        self.current = list(current)
        self.saved = saved if saved is not None else {"cookies": self.current}
        self.added = []

    def cookies(self):
        return self.current

    def add_cookies(self, cookies):
        self.added.extend(cookies)

    def storage_state(self):
        return self.saved


def write_state(path, cookies):
    path.write_text(json.dumps({"cookies": cookies}), encoding="utf-8")


def test_live_cookie_wins_over_stale_snapshot(tmp_path):
    path = tmp_path / "state.json"
    write_state(path, [cookie(value="old")])
    context = Context([cookie(value="fresh")])
    log = []
    session.load_session_state(context, str(path), log)
    assert context.added == []
    assert context.current[0]["value"] == "fresh"
    assert log == ["STATE_LOADED(0)"]


def test_live_cookie_domain_case_and_leading_dot_do_not_allow_overwrite(tmp_path):
    path = tmp_path / "state.json"
    write_state(path, [cookie(domain="TISTORY.COM", value="old")])
    context = Context([cookie(value="fresh")])
    session.load_session_state(context, str(path), [])
    assert context.added == []


def test_only_missing_session_cookie_is_restored_and_deduplicated(tmp_path):
    path = tmp_path / "state.json"
    missing = cookie(name="missing", domain="accounts.kakao.com", expires=0)
    write_state(path, [cookie(), missing, missing, cookie(name="persistent", expires=2000000000)])
    context = Context([cookie(value="fresh")])
    log = []
    session.load_session_state(context, str(path), log)
    assert context.added == [missing]
    assert log == ["STATE_LOADED(1)"]


def test_cookie_path_and_partition_key_keep_distinct_identities(tmp_path):
    path = tmp_path / "state.json"
    distinct_path = cookie(path="/manage")
    distinct_partition = cookie(partitionKey="https://tistory.com")
    write_state(path, [cookie(), distinct_path, distinct_partition])
    context = Context([cookie()])
    session.load_session_state(context, str(path), [])
    assert context.added == [distinct_path, distinct_partition]


@pytest.mark.parametrize("domain", [
    "tistory.com.evil.invalid", "eviltistory.com", "evil-kakao.com",
    "kakao.com.evil.invalid", "..kakao.com", ".tistory.com.", "example.com",
    "evil/@foo.kakao.com", "https://foo.tistory.com", "evil space.kakao.com",
    "evil@foo.kakao.com", "-invalid.tistory.com", "invalid-.kakao.com",
])
def test_domain_suffix_attack_is_neither_restored_nor_saved(tmp_path, domain):
    path = tmp_path / "state.json"
    write_state(path, [cookie(domain=domain)])
    context = Context(saved={"cookies": [cookie(domain=domain)]})
    session.load_session_state(context, str(path), [])
    assert context.added == []
    assert session.save_session_state(context, str(path), [])
    assert json.loads(path.read_text())["cookies"] == []


def test_save_keeps_scoped_cookies_only_and_omits_origin_storage(tmp_path):
    path = tmp_path / "state.json"
    allowed = [cookie(), cookie(domain="www.tistory.com"), cookie(domain="kakao.com"),
               cookie(domain="accounts.kakao.com", expires=2000000000)]
    context = Context(saved={"cookies": allowed + [cookie(domain="example.com")],
                             "origins": [{"origin": "https://tistory.com",
                                          "localStorage": "not-to-be-saved"}]})
    log = []
    assert session.save_session_state(context, str(path), log)
    assert json.loads(path.read_text()) == {"cookies": allowed, "origins": []}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert log == ["STATE_SAVED"]


def test_load_missing_or_invalid_state_never_logs_contents(tmp_path):
    path = tmp_path / "state.json"
    context = Context()
    log = []
    session.load_session_state(context, str(path), log)
    assert log == ["STATE_EMPTY"]
    path.write_text("synthetic-secret-not-valid-json")
    log.clear()
    session.load_session_state(context, str(path), log)
    assert log == ["STATE_FAIL:JSONDecodeError"]
    assert context.added == []


@pytest.mark.parametrize("operation", ["cookies", "add_cookies", "storage_state"])
def test_browser_exceptions_log_type_only(tmp_path, monkeypatch, operation):
    path = tmp_path / "state.json"
    write_state(path, [cookie()])
    context = Context()

    def fail(*args):
        raise RuntimeError("synthetic-secret-browser-error")

    monkeypatch.setattr(context, operation, fail)
    log = []
    if operation == "storage_state":
        assert not session.save_session_state(context, str(path), log)
        assert log == ["STATE_SAVE_FAIL:RuntimeError"]
    else:
        session.load_session_state(context, str(path), log)
        assert log == ["STATE_FAIL:RuntimeError"]


@pytest.mark.parametrize("operation", ["fsync", "replace"])
def test_failed_atomic_save_preserves_previous_state(tmp_path, monkeypatch, operation):
    path = tmp_path / "state.json"
    original = b"synthetic-existing-state"
    path.write_bytes(original)
    context = Context([cookie(value="new")])

    def fail(*args):
        raise OSError("synthetic-secret-os-error")

    monkeypatch.setattr(session.os, operation, fail)
    log = []
    assert not session.save_session_state(context, str(path), log)
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
    assert log == ["STATE_SAVE_FAIL:OSError"]


def test_replacement_also_restricts_existing_file_permissions(tmp_path):
    path = tmp_path / "state.json"
    write_state(path, [])
    path.chmod(0o644)
    assert session.save_session_state(Context([cookie()]), str(path), [])
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("expires", [float("nan"), float("inf"), "-1", True])
def test_invalid_expiration_is_not_restored(tmp_path, expires):
    path = tmp_path / "state.json"
    write_state(path, [cookie(expires=expires)])
    context = Context()
    session.load_session_state(context, str(path), [])
    assert context.added == []
