"""티스토리 편집 URL 해소(2026-09-06) — 발행된 글을 새 글로 또 올리지 않고 같은 URL 을 고친다."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blog"))


def _m():
    return importlib.import_module("blog.tistory_publish_pw")


def test_resolve_editor_url_returns_newpost_without_post_id():
    m = _m()
    assert m.resolve_editor_url() == m.NEWPOST_URL
    assert m.resolve_editor_url(None) == m.NEWPOST_URL
    assert m.resolve_editor_url("") == m.NEWPOST_URL


def test_resolve_editor_url_appends_post_id_with_single_slash():
    m = _m()
    url = m.resolve_editor_url("79")
    assert url == m.NEWPOST_URL.rstrip("/") + "/79/"
    assert "//79" not in url.replace("https://", "")
    assert m.resolve_editor_url("/79/") == url          # 슬래시가 붙어 와도 같은 URL
    assert m.resolve_editor_url(79) == url              # 정수도 허용(CLI 밖 호출)
