"""본문이미지계약(BodyMedia, 2026-10-10) — 원고 본문 이미지의 sidecar·사이트 서빙 확인·편집기 재호스팅 대조."""
import hashlib
import json
import struct
import zlib

import pytest

from blog import body_media as bm
from blog.build_site import BASE_URL
from blog.tistory_contract import parse_helper
from blog.tistory_draft import write_digest_draft

NAME = "dashboard-2026-10-10-0123456789abcdef.png"


def _png(width=1080, height=1470, shade=b"\xff", level=6):
    raw = b"".join(b"\x00" + shade * (width * 3) for _ in range(height))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, level)) + chunk(b"IEND", b""))


def _setup(tmp_path, payload=None, name=NAME):
    payload = payload if payload is not None else _png()
    (tmp_path / "images").mkdir(exist_ok=True)
    (tmp_path / "images" / name).write_bytes(payload)
    url = f"{BASE_URL}/images/{name}"
    body = f'<p>요약</p><p><img src="{url}" width="1080" height="1470" alt="대시보드"></p><p>표</p>'
    draft = write_digest_draft({"title": "제목", "tags": "서울", "tistory_html": body}, "2026-10-10", str(tmp_path))
    entry = {"url": url, "image": f"../images/{name}", "sha256": hashlib.sha256(payload).hexdigest(),
             "width": 1080, "height": 1470}
    return draft, entry, payload


def test_bound_sidecar_round_trips_exact_bytes(tmp_path):
    draft, entry, payload = _setup(tmp_path)
    path = bm.bind_body_media(draft, [entry])
    assert path.endswith(".body-media.json")
    images = bm.read_body_media(draft, parse_helper(draft))
    assert [(i.url, i.name, i.width, i.height, i.image_bytes) for i in images] == \
           [(entry["url"], NAME, 1080, 1470, payload)]


def test_no_sidecar_is_none_only_for_bodies_without_images(tmp_path):
    plain = write_digest_draft({"title": "제목", "tags": "서울", "tistory_html": "<p>본문</p>"}, "2026-10-11", str(tmp_path))
    assert bm.read_body_media(plain, parse_helper(plain)) is None
    draft, entry, _ = _setup(tmp_path)
    with pytest.raises(bm.BodyMediaError):
        bm.read_body_media(draft, parse_helper(draft))   # 본문에 img 가 있는데 계약이 없으면 붙여넣지 않는다
    bm.bind_body_media(draft, [entry])
    assert bm.bind_body_media(draft, None) is None and not bm.body_media_path(draft).exists()


@pytest.mark.parametrize("tamper", ["bytes", "size", "payload", "url_missing", "traversal", "duplicate_key"])
def test_any_mismatch_fails_closed(tmp_path, tamper):
    draft, entry, payload = _setup(tmp_path)
    if tamper == "size":
        entry = {**entry, "height": 1000}
        with pytest.raises(bm.BodyMediaError):
            bm.bind_body_media(draft, [entry])
        return
    if tamper == "traversal":
        with pytest.raises(bm.BodyMediaError):
            bm.bind_body_media(draft, [{**entry, "image": "../../x/" + NAME}])
        return
    if tamper == "url_missing":
        with pytest.raises(bm.BodyMediaError):
            bm.bind_body_media(draft, [{**entry, "url": f"{BASE_URL}/images/other.png"}])
        return
    sidecar = bm.bind_body_media(draft, [entry])
    data = parse_helper(draft)
    if tamper == "bytes":
        (tmp_path / "images" / NAME).write_bytes(_png(1080, 1471))
    elif tamper == "payload":
        data = {**data, "body": data["body"].replace("요약", "변경")}
    elif tamper == "duplicate_key":
        text = open(sidecar, encoding="utf-8").read()
        open(sidecar, "w", encoding="utf-8").write(text.replace('{"draft"', '{"draft": "x", "draft"', 1))
    with pytest.raises(bm.BodyMediaError):
        bm.read_body_media(draft, data)


def _image(width=1080, height=1470, payload=None):
    payload = payload if payload is not None else _png(width, height)
    return bm.BodyImage(url="u", name=NAME, sha256=hashlib.sha256(payload).hexdigest(), width=width, height=height,
                        image_bytes=payload)


CDN = "https://blog.kakaocdn.net/dna/a/img.png?x=1"
LOADED = {"complete": True, "nw": 1080, "nh": 1470}


@pytest.mark.parametrize("dom,state", [
    ([], "count"),
    ([{"src": "data:image/png;base64,AAAA", "w": None, "h": None, **LOADED}], "pending"),
    ([{"src": CDN, "w": "1080", "h": "1470", **LOADED}], "ok"),
    ([{"src": CDN, "w": "1200", "h": "630", **LOADED}], "size"),
    ([{"src": CDN, "w": "1080", "h": "1470", "complete": True, "nw": 1200, "nh": 630}], "size"),
    ([{"src": CDN, "w": "1080", "h": "1470", "complete": False, "nw": 0, "nh": 0}], "pending"),   # 아직 받는 중
    ([{"src": CDN, "w": "1080", "h": "1470", "complete": True, "nw": 0, "nh": 0}], "broken"),    # 받았지만 깨짐(404 등)
    ([{"src": "https://evil.example/img.png", "w": "1080", "h": "1470", **LOADED}], "pending"),
    ([{"src": CDN, "w": "1080", "h": "1470", **LOADED}] * 2, "count"),
])
def test_editor_image_state_requires_rehosted_kakao_cdn_with_origin_size(dom, state):
    assert bm.editor_image_state(dom, [_image()]) == state


def test_verify_polls_until_rehosted_then_times_out_closed(monkeypatch):
    monkeypatch.setattr(bm, "fetch_rehosted_image", lambda src: _png())
    seq = iter([[{"src": "data:x", "w": None, "h": None, "complete": True, "nw": 1080, "nh": 1470}],
                [{"src": CDN, "w": "1080", "h": "1470", "complete": False, "nw": 0, "nh": 0}],
                [{"src": CDN, "w": "1080", "h": "1470", **LOADED}]])

    class Page:
        def evaluate(self, script):
            return next(seq)

        def wait_for_timeout(self, ms):
            pass

    assert bm.verify_body_images(Page(), [_image()], timeout_ms=2000, poll_ms=500) == "ok"

    class Stuck(Page):
        def evaluate(self, script):
            return [{"src": "data:x", "w": None, "h": None}]

    assert bm.verify_body_images(Stuck(), [_image()], timeout_ms=1500, poll_ms=500) == "pending"


OTHER = _png(shade=b"\x10")   # 같은 크기·다른 그림(전날 그래프 등)


@pytest.mark.parametrize("served, state", [
    (_png(), "ok"),                       # 카카오 CDN 은 올린 바이트를 그대로 준다(10-10 실측 sha 일치)
    (_png(level=1), "ok"),                # 무손실 재압축 — 바이트는 달라도 디코드 픽셀이 같으면 같은 그림
    (OTHER, "content"),                   # 같은 크기의 다른 그림
    (b"not a png", "content"),
], ids=["same-bytes", "lossless-recompress", "other-picture", "not-png"])
def test_rehosted_image_must_match_expected_pixels(monkeypatch, served, state):
    seen = []
    monkeypatch.setattr(bm, "fetch_rehosted_image", lambda src: seen.append(src) or served)
    dom = [{"src": CDN, "w": "1080", "h": "1470", **LOADED}]
    assert bm.compare_rehosted_images(dom, [_image()]) == state
    assert seen == [CDN]


def test_rehosted_images_are_bound_in_order_and_fetch_failure_is_closed(monkeypatch):
    first, second = _png(), OTHER
    served = {"https://blog.kakaocdn.net/1": second, "https://blog.kakaocdn.net/2": first}
    monkeypatch.setattr(bm, "fetch_rehosted_image", served.__getitem__)
    dom = [{"src": "https://blog.kakaocdn.net/1", **LOADED}, {"src": "https://blog.kakaocdn.net/2", **LOADED}]
    assert bm.compare_rehosted_images(dom, [_image(payload=first), _image(payload=second)]) == "content"

    def unreachable(src):
        raise OSError("timeout")
    monkeypatch.setattr(bm, "fetch_rehosted_image", unreachable)
    assert bm.compare_rehosted_images(dom[:1], [_image()]) == "fetch"


def test_verify_returns_content_mismatch_after_rehost_looks_ok(monkeypatch):
    monkeypatch.setattr(bm, "fetch_rehosted_image", lambda src: OTHER)

    class Page:
        def evaluate(self, script):
            return [{"src": CDN, "w": "1080", "h": "1470", **LOADED}]

        def wait_for_timeout(self, ms):
            raise AssertionError("no polling once rehost state is ok")

    assert bm.verify_body_images(Page(), [_image()]) == "content"


def test_fetch_refuses_anything_but_kakao_cdn():
    with pytest.raises(bm.BodyMediaError):
        bm.fetch_rehosted_image("https://evil.example/img.png")


def _apng(first: bytes, second: bytes) -> bytes:
    import io
    from PIL import Image
    a, b = Image.open(io.BytesIO(first)), Image.open(io.BytesIO(second))
    out = io.BytesIO()
    a.save(out, "PNG", save_all=True, append_images=[b])
    return out.getvalue()


def test_animated_png_with_matching_first_frame_is_not_the_same_picture(monkeypatch):
    animated = _apng(_png(), OTHER)
    monkeypatch.setattr(bm, "fetch_rehosted_image", lambda src: animated)
    dom = [{"src": CDN, "w": "1080", "h": "1470", **LOADED}]
    assert bm.compare_rehosted_images(dom, [_image()]) == "content"


def test_fetch_refuses_redirects_and_final_urls_outside_kakao_cdn(monkeypatch):
    import urllib.request
    handler = bm._CdnOnlyRedirect()
    request = urllib.request.Request(CDN)
    with pytest.raises(bm.BodyMediaError):
        handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example/img.png")
    assert handler.redirect_request(request, None, 302, "Found", {}, CDN + "&r=1").full_url == CDN + "&r=1"

    class Response:
        def __init__(self, final):
            self.final = final

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return self.final

        def read(self, limit):
            return _png()

    class Opener:
        def __init__(self, final):
            self.final = final

        def open(self, request, timeout):
            return Response(self.final)

    monkeypatch.setattr(bm, "_OPENER", Opener("https://evil.example/img.png"))
    with pytest.raises(bm.BodyMediaError):
        bm.fetch_rehosted_image(CDN)
    monkeypatch.setattr(bm, "_OPENER", Opener(CDN))
    assert bm.fetch_rehosted_image(CDN) == _png()


SOURCE = f"{BASE_URL}/images/{NAME}"


def _source_image(payload=None):
    payload = payload if payload is not None else _png()
    return bm.BodyImage(url=SOURCE, name=NAME, sha256=hashlib.sha256(payload).hexdigest(), width=1080, height=1470,
                        image_bytes=payload)


def test_source_wait_polls_until_site_serves_the_bound_picture(monkeypatch):
    """편집기는 원고 URL 을 받아 재호스팅한다(data URI 는 attach.json 413, 2026-10-13 실측) — 붙이기 전 사이트 서빙 확인."""
    served = iter([OSError("404"), OTHER, _png()])
    seen, slept = [], []

    def fetch(url):
        seen.append(url)
        item = next(served)
        if isinstance(item, Exception):
            raise item
        return item
    monkeypatch.setattr(bm, "fetch_source_image", fetch)
    assert bm.wait_for_source_images([_source_image()], timeout_s=60, poll_s=10, sleep=slept.append) == "ok"
    assert seen == [SOURCE] * 3 and slept == [10, 10]


@pytest.mark.parametrize("served, state", [(None, "fetch"), (OTHER, "content")], ids=["missing", "other-picture"])
def test_source_wait_times_out_closed(monkeypatch, served, state):
    def fetch(url):
        if served is None:
            raise OSError("404")
        return served
    monkeypatch.setattr(bm, "fetch_source_image", fetch)
    slept = []
    assert bm.wait_for_source_images([_source_image()], timeout_s=30, poll_s=10, sleep=slept.append) == state
    assert slept == [10, 10, 10]


def test_source_fetch_refuses_urls_and_redirects_outside_site_images(monkeypatch):
    for url in ("https://evil.example/images/" + NAME, f"{BASE_URL}/{NAME}", f"{BASE_URL}/images/../x.png"):
        with pytest.raises(bm.BodyMediaError):
            bm.fetch_source_image(url)
    import urllib.request
    handler = bm._SourceOnlyRedirect()
    with pytest.raises(bm.BodyMediaError):
        handler.redirect_request(urllib.request.Request(SOURCE), None, 302, "Found", {}, "https://evil.example/x.png")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://evil.example/x.png"

        def read(self, limit):
            return _png()

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(bm, "_SOURCE_OPENER", Opener())
    with pytest.raises(bm.BodyMediaError):
        bm.fetch_source_image(SOURCE)
