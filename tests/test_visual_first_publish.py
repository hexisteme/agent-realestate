"""시각 우선 본문 이미지 발행 경계(2026-10-10) — 계약 없는 이미지 본문 차단, 클릭·시도 마커 전 이미지 대조."""
import datetime
import html
import inspect
import sys
import types
from unittest.mock import MagicMock, Mock

import pytest

from blog import tistory_publish_pw as pub


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    class Today(datetime.date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 10)
    monkeypatch.setattr(pub.datetime, "date", Today)
    monkeypatch.setattr(pub, "PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(pub, "STATE_FILE", str(tmp_path / "profile/state.json"))
    monkeypatch.setattr(pub, "_notify_failure", Mock())
    return tmp_path


def test_image_body_without_contract_stops_before_browser(isolated, monkeypatch):
    body = '<p>요약</p><p><img src="https://example.invalid/images/dashboard.png"></p>'
    path = isolated / "report/blog/tistory/2026-10-10-tistory-draft.html"
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"<textarea id={k}>{html.escape(v)}</textarea>"
                            for k, v in zip(("t", "b", "g"), ("제목", body, "서울"))))
    monkeypatch.setattr(pub, "_publish_locked", Mock(side_effect=AssertionError("browser reached")))
    assert pub.publish(str(isolated), "publish", "2026-10-10") == "ERR:body image contract invalid"


def test_body_images_are_verified_before_tags_inject_return_and_attempt_marker():
    source = inspect.getsource(pub._publish_locked)
    verify = source.index("verify_body_images(page, body_images)")
    assert source.index('log.append("PASTE_VERIFIED")') < verify < source.index("# 5) 태그")
    assert verify < source.index('if mode == "inject":') < source.index('state="ATTEMPTED"')
    assert "embed_body_images" not in source and "data:image" not in source
    assert source.index("wait_for_source_images(body_images)") < source.index("sync_playwright")


def _image_draft(root):
    from test_body_media import _setup
    from blog import body_media as bm
    blog_dir = root / "report/blog"
    blog_dir.mkdir(parents=True, exist_ok=True)
    draft, entry, _ = _setup(blog_dir)
    bm.bind_body_media(draft, [entry])
    return draft


class _Calls(list):
    clipboard: list


def _fake_browser(monkeypatch, editor_images):
    """편집기 DOM 만 흉내 낸다. evaluate 는 스크립트 종류별로 답하고 모든 호출을 기록한다."""
    calls, clipboard = _Calls(), []
    page = MagicMock()
    page.url = "https://floker.tistory.com/manage/newpost"

    def evaluate(script, *args):
        calls.append(script)
        if "clipboard.write" in script:
            clipboard.extend(args)
        if "querySelectorAll('img')" in script:
            return editor_images(len(calls))
        if "innerHTML.length" in script:
            return 5000
        if "publish-btn" in script:
            return "id"
        return True
    page.evaluate.side_effect = evaluate
    ctx = MagicMock()
    ctx.pages = [page]
    p = MagicMock()
    p.chromium.launch_persistent_context.return_value = ctx
    manager = MagicMock()
    manager.__enter__.return_value = p
    package, api = types.ModuleType("playwright"), types.ModuleType("playwright.sync_api")
    api.sync_playwright = Mock(return_value=manager)
    package.sync_api = api
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    for name, value in (("_load_state", Mock()), ("_save_state", Mock()), ("_refresh_kakao_session", Mock()),
                        ("save_session_state", Mock(return_value=True)), ("pasted_anchors_present", Mock(return_value=True)),
                        ("_verify_published_on_blog", Mock(return_value=False)), ("_gate_design_review", Mock(return_value=None)),
                        ("wait_for_source_images", Mock(return_value="ok"))):
        monkeypatch.setattr(pub, name, value)
    monkeypatch.setattr("subprocess.run", Mock())
    calls.clipboard = clipboard
    return calls


def test_unverified_body_image_stops_before_attempt_marker_and_publish_click(isolated, monkeypatch):
    _image_draft(isolated)
    broken = [{"src": "https://blog.kakaocdn.net/dna/a/img.png", "w": "1080", "h": "1470",
               "complete": True, "nw": 0, "nh": 0}]
    calls = _fake_browser(monkeypatch, lambda n: [] if n < 4 else broken)
    monkeypatch.setattr(pub, "verify_body_images",
                        lambda page, images: __import__("blog.body_media").body_media.editor_image_state(broken, images))
    result = pub.publish(str(isolated), "publish", "2026-10-10")
    assert "ERR:body_image_unverified(broken)" in result
    assert not any("publish-layer-btn" in c or "publish-btn" in c for c in calls)
    from blog.tistory_delivery import read_delivery
    assert read_delivery(str(isolated), "2026-10-10", "daily") is None
    assert not (isolated / pub.marker_paths(str(isolated), "daily")[1]).exists()


def test_leftover_editor_image_stops_before_clipboard_write(isolated, monkeypatch):
    _image_draft(isolated)
    stale = [{"src": "https://blog.kakaocdn.net/dna/old/img.png", "w": "1080", "h": "1470",
              "complete": True, "nw": 1080, "nh": 1470}]
    calls = _fake_browser(monkeypatch, lambda n: stale)
    result = pub.publish(str(isolated), "inject", "2026-10-10")
    assert "ERR:editor_not_empty_before_paste" in result
    assert not any("clipboard.write" in c for c in calls)


@pytest.mark.parametrize("fresh, expected", [(True, "BODY_IMAGES_VERIFIED:1"),
                                             (False, "ERR:body_image_unverified(content)")])
def test_rehosted_image_content_is_bound_after_paste(isolated, monkeypatch, fresh, expected):
    """클립보드 기록과 Meta+V 사이에 이전 본문(같은 글자·같은 크기의 이전 그래프)이 들어와도 발행까지 못 간다."""
    from test_body_media import _png
    from blog import body_media as bm
    _image_draft(isolated)
    cdn = "https://blog.kakaocdn.net/dna/x/img.png"
    rehosted = [{"src": cdn, "w": "1080", "h": "1470", "complete": True, "nw": 1080, "nh": 1470}]
    box = {}
    box["calls"] = calls = _fake_browser(
        monkeypatch, lambda n: rehosted if any("clipboard.write" in c for c in box["calls"]) else [])
    fetched = []
    monkeypatch.setattr(bm, "fetch_rehosted_image",
                        lambda src: fetched.append(src) or (_png() if fresh else _png(shade=b"\x10")))
    result = pub.publish(str(isolated), "publish", "2026-10-10")
    assert expected in result and fetched == [cdn]
    if not fresh:
        assert not any("publish-layer-btn" in c or "publish-btn" in c for c in calls)
        from blog.tistory_delivery import read_delivery
        assert read_delivery(str(isolated), "2026-10-10", "daily") is None
        assert not (isolated / pub.marker_paths(str(isolated), "daily")[1]).exists()


def test_body_is_pasted_with_site_urls_not_data_uris(isolated, monkeypatch):
    """data URI 본문은 attach.json 413 으로 붙여넣기 전체가 버려졌다(10-11~10-13 PASTE_BASIC:30) — 원고 그대로 붙인다."""
    from blog.tistory_contract import parse_helper
    draft = _image_draft(isolated)
    calls = _fake_browser(monkeypatch, lambda n: [])
    pub.publish(str(isolated), "inject", "2026-10-10")
    assert calls.clipboard == [parse_helper(draft)["body"]]
    assert "data:image" not in calls.clipboard[0]


@pytest.mark.parametrize("state", ["fetch", "content"])
def test_site_not_serving_bound_image_stops_before_browser(isolated, monkeypatch, state):
    _image_draft(isolated)
    _fake_browser(monkeypatch, lambda n: [])
    monkeypatch.setattr(pub, "wait_for_source_images", Mock(return_value=state))
    launch = sys.modules["playwright.sync_api"].sync_playwright
    result = pub.publish(str(isolated), "publish", "2026-10-10")
    assert f"ERR:body_image_source_unready({state})" in result
    launch.assert_not_called()
    from blog.tistory_delivery import read_delivery
    assert read_delivery(str(isolated), "2026-10-10", "daily") is None
