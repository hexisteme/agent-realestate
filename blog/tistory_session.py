"""Restore missing Tistory session cookies and persist their current state."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path


def _cookie_domain(cookie: dict) -> str | None:
    domain = cookie.get("domain")
    if not isinstance(domain, str):
        return None
    domain = domain.lower().removeprefix(".")
    if any(domain == root or domain.endswith("." + root)
           for root in ("tistory.com", "kakao.com")):
        if len(domain) <= 253 and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in domain.split(".")
        ):
            return domain
    return None


def _cookie_key(cookie: dict) -> tuple:
    """Match browser cookie identity without using or logging its value."""
    return (
        cookie["name"],
        cookie["domain"].lower().removeprefix("."),
        cookie["path"],
        json.dumps(cookie.get("partitionKey"), sort_keys=True, separators=(",", ":")),
    )


def _scoped_cookies(state: dict) -> list[dict]:
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
        raise ValueError("invalid cookie state")
    cookies = []
    for cookie in state["cookies"]:
        if not isinstance(cookie, dict) or _cookie_domain(cookie) is None:
            continue
        if (not isinstance(cookie.get("name"), str) or not cookie["name"]
                or not isinstance(cookie.get("value"), str)
                or not isinstance(cookie.get("path"), str)
                or not cookie["path"].startswith("/")):
            raise ValueError("invalid scoped cookie")
        cookies.append(cookie)
    return cookies


def load_session_state(ctx, state_file: str, log: list[str]) -> None:
    """Restore saved session cookies only when their identity is absent now.

    Chromium's live cookies take precedence over the saved snapshot, including
    cookies refreshed by a manual sign-in. Persistent cookies stay in Chromium's
    profile. Storage origins are never restored.
    """
    try:
        with open(state_file, encoding="utf-8") as source:
            state = json.load(source)
    except FileNotFoundError:
        log.append("STATE_EMPTY")
        return
    except Exception as exc:
        log.append(f"STATE_FAIL:{type(exc).__name__}")
        return
    try:
        saved = _scoped_cookies(state)
        current_keys = {_cookie_key(cookie) for cookie in ctx.cookies()}
        missing = []
        for cookie in saved:
            expires = cookie.get("expires", -1)
            if (isinstance(expires, bool) or not isinstance(expires, (int, float))
                    or not math.isfinite(expires) or expires > 0):
                continue
            key = _cookie_key(cookie)
            if key not in current_keys:
                missing.append(cookie)
                current_keys.add(key)
        if missing:
            ctx.add_cookies(missing)
        log.append(f"STATE_LOADED({len(missing)})")
    except Exception as exc:
        log.append(f"STATE_FAIL:{type(exc).__name__}")


def save_session_state(ctx, state_file: str, log: list[str]) -> bool:
    """Atomically save scoped cookies in a mode-600 file, without local storage."""
    temporary = None
    try:
        cookies = _scoped_cookies(ctx.storage_state())
        payload = json.dumps({"cookies": cookies, "origins": []}, allow_nan=False)
        target = Path(state_file)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        temporary = None
        log.append("STATE_SAVED")
        return True
    except Exception as exc:
        log.append(f"STATE_SAVE_FAIL:{type(exc).__name__}")
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass
