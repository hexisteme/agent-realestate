"""본문이미지계약(BodyMedia, 2026-10-10) — 원고 본문 <img> 를 로컬 sha 검증 바이트에 묶는다.

<원고>.body-media.json = 원고 본문 해시 + 이미지마다 (원고 URL, ../images/이름, sha256, 원본 폭·높이).
발행기는 사이트(Pages)가 원고 URL 에서 같은 그림을 서빙하는지 확인한 뒤 원고 본문을 그대로 붙여넣고
(편집기가 그 URL 을 받아 카카오 CDN 에 올린다 — data URI 본문은 attach.json 413 으로 붙여넣기 전체가 버려진다,
2026-10-13 실측), 발행 클릭 전에 편집기 안 이미지가 카카오 CDN 으로 재호스팅됐는지(수·순서·원본 크기) 대조하고,
재호스팅된 파일을 내려받아 기대 PNG 와 같은 그림인지(sha256, 다르면 디코드 픽셀) 순서대로 결박한다.
티스토리는 404 이미지를 조용히 지우고 재호스팅은 비동기라, 붙여넣기 글자 대조만으로는 이미지 소실을 못 잡는다."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import re
import struct
import tempfile
import time
import urllib.request

from blog.tistory_delivery import payload_digest

SCHEMA = "TistoryBodyMedia/v1"
_NAME = re.compile(r"(?:dashboard|gu-reach|gu-axis)-\d{4}-\d{2}-\d{2}-[0-9a-f]{16}\.png")   # gu-* = 구 원고 K·W 그림(2026-10-22 gu_dikw)
_KAKAO_CDN = re.compile(r"https://blog\.kakaocdn\.net/")
_MAX_SIDECAR = 16_384
_MAX_IMAGE = 4_000_000
_MAX_IMAGES = 4
_EDITOR_IMAGES = """() => { const f = document.getElementById('editor-tistory_ifr');
    const d = f && (f.contentDocument || f.contentWindow.document);
    if (!d) return [];
    return [].slice.call(d.querySelectorAll('img')).map(i => ({src: i.getAttribute('src') || '',
        w: i.getAttribute('data-origin-width'), h: i.getAttribute('data-origin-height'),
        complete: i.complete, nw: i.naturalWidth, nh: i.naturalHeight})); }"""


class BodyMediaError(ValueError):
    """고정 문구 — 파일 내용·경로를 싣지 않는다."""

    def __init__(self):
        super().__init__("body image contract invalid")


@dataclass(frozen=True)
class BodyImage:
    url: str
    name: str
    sha256: str
    width: int
    height: int
    image_bytes: bytes = field(repr=False)


def body_media_path(helper: str | Path) -> Path:
    return Path(helper).with_suffix(".body-media.json")


class _ImageSources(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.sources: list[str | None] = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            values = [value for key, value in attrs if key == "src"]
            self.sources.append(values[0] if len(values) == 1 else None)


def _body_sources(body: str) -> list[str | None]:
    parser = _ImageSources()
    parser.feed(body)
    parser.close()
    return parser.sources


def _png_size(payload: bytes) -> tuple[int, int]:
    if payload[:8] != b"\x89PNG\r\n\x1a\n" or payload[12:16] != b"IHDR":
        raise BodyMediaError()
    return struct.unpack(">II", payload[16:24])


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BodyMediaError()
        result[key] = value
    return result


def _validate(helper: Path, data: dict, record: dict) -> list[BodyImage]:
    from blog.build_site import BASE_URL
    if (not isinstance(record, dict) or set(record) != {"schema", "draft", "payload_sha256", "images"}
            or record["schema"] != SCHEMA or record["draft"] != helper.name
            or record["payload_sha256"] != payload_digest(data)):
        raise BodyMediaError()
    entries = record["images"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= _MAX_IMAGES:
        raise BodyMediaError()
    image_dir = (helper.parent.parent / "images").resolve()
    images = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"url", "image", "sha256", "width", "height"}:
            raise BodyMediaError()
        name = entry["image"][len("../images/"):] if isinstance(entry["image"], str) else ""
        if (not entry["image"].startswith("../images/") or not _NAME.fullmatch(name)
                or entry["url"] != f"{BASE_URL}/images/{name}"
                or not all(isinstance(entry[k], int) and not isinstance(entry[k], bool) and entry[k] > 0
                           for k in ("width", "height"))):
            raise BodyMediaError()
        path = image_dir / name
        if path.is_symlink() or not path.is_file() or path.resolve().parent != image_dir \
                or path.stat().st_size > _MAX_IMAGE:
            raise BodyMediaError()
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != entry["sha256"] or _png_size(payload) != (entry["width"], entry["height"]):
            raise BodyMediaError()
        images.append(BodyImage(entry["url"], name, entry["sha256"], entry["width"], entry["height"], payload))
    if _body_sources(data["body"]) != [image.url for image in images]:
        raise BodyMediaError()
    return images


def bind_body_media(helper: str | Path, entries: list[dict] | None) -> str | None:
    """sidecar 를 원자적으로 쓰고 다시 읽어 검증한다. entries 가 없으면 남은 sidecar 를 지운다(예전 실행 잔재 차단)."""
    from blog.tistory_contract import parse_helper
    helper = Path(helper).resolve()
    destination = body_media_path(helper)
    if not entries:
        destination.unlink(missing_ok=True)
        return None
    data = parse_helper(helper)
    record = {"schema": SCHEMA, "draft": helper.name, "payload_sha256": payload_digest(data), "images": list(entries)}
    try:
        _validate(helper, data, record)
    except BodyMediaError:
        raise
    except Exception:
        raise BodyMediaError() from None
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    read_body_media(helper, data)
    return str(destination)


def read_body_media(helper: str | Path, data: dict) -> list[BodyImage] | None:
    """sidecar 가 없고 본문에 이미지도 없으면 None. 본문에 이미지가 있는데 계약이 없거나 하나라도 어긋나면 BodyMediaError."""
    try:
        helper = Path(helper).resolve()
        sidecar = body_media_path(helper)
        if not sidecar.exists():
            if _body_sources(data["body"]):
                raise BodyMediaError()
            return None
        if sidecar.is_symlink() or sidecar.stat().st_size > _MAX_SIDECAR:
            raise BodyMediaError()
        record = json.loads(sidecar.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
        return _validate(helper, data, record)
    except BodyMediaError:
        raise
    except Exception:
        raise BodyMediaError() from None


def read_editor_images(page) -> list[dict]:
    return page.evaluate(_EDITOR_IMAGES)


def editor_image_state(dom: list[dict], images: list[BodyImage]) -> str:
    """편집기 img 목록 대조 — ok | count(수 불일치) | pending(재호스팅·내려받기 전) | broken(받았는데 못 그림)
    | size(원본 크기 불일치). 속성(data-origin-*)만이 아니라 실제로 디코드된 크기(naturalWidth/Height)까지 본다."""
    if not isinstance(dom, list) or len(dom) != len(images):
        return "count"
    if not all(isinstance(item, dict) and _KAKAO_CDN.match(str(item.get("src") or "")) for item in dom):
        return "pending"
    if not all(item.get("complete") is True for item in dom):
        return "pending"
    if not all(item.get("nw") and item.get("nh") for item in dom):
        return "broken"
    for item, image in zip(dom, images):
        if ((str(item.get("w")), str(item.get("h"))) != (str(image.width), str(image.height))
                or (item.get("nw"), item.get("nh")) != (image.width, image.height)):
            return "size"
    return "ok"


def _source_allowed(url: str) -> bool:
    """원고 이미지 URL 은 사이트 images/ 바로 아래 계약 파일명만 허용한다."""
    from blog.build_site import BASE_URL
    prefix = f"{BASE_URL}/images/"
    return url.startswith(prefix) and bool(_NAME.fullmatch(url[len(prefix):]))


class _CdnOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트도 카카오 CDN 안에서만 따라간다(밖으로 나가는 Location 은 요청 전에 거부)."""
    allowed = staticmethod(lambda url: bool(_KAKAO_CDN.match(url)))

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allowed(newurl):
            raise BodyMediaError()
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _SourceOnlyRedirect(_CdnOnlyRedirect):
    """원고 이미지 확인도 사이트 images/ 밖으로는 따라가지 않는다."""
    allowed = staticmethod(_source_allowed)


_OPENER = urllib.request.build_opener(_CdnOnlyRedirect)
_SOURCE_OPENER = urllib.request.build_opener(_SourceOnlyRedirect)


def _fetch_pinned(src: str, allowed, opener, timeout: float) -> bytes:
    if not allowed(src):
        raise BodyMediaError()
    request = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(request, timeout=timeout) as response:
        if not allowed(response.geturl()):
            raise BodyMediaError()
        payload = response.read(_MAX_IMAGE + 1)
    if len(payload) > _MAX_IMAGE:
        raise BodyMediaError()
    return payload


def fetch_rehosted_image(src: str, timeout: float = 15) -> bytes:
    """카카오 CDN 에 재호스팅된 이미지 바이트. CDN 밖 주소·최종 URL·과대 응답은 거부한다(주소는 오류에 싣지 않는다)."""
    return _fetch_pinned(src, _CdnOnlyRedirect.allowed, _OPENER, timeout)


def fetch_source_image(url: str, timeout: float = 15) -> bytes:
    """사이트가 원고 이미지 URL 에서 지금 서빙하는 바이트. images/ 밖 주소·최종 URL·과대 응답은 거부한다."""
    return _fetch_pinned(url, _SourceOnlyRedirect.allowed, _SOURCE_OPENER, timeout)


def _same_picture(payload: bytes, image: BodyImage) -> bool:
    if hashlib.sha256(payload).hexdigest() == image.sha256:
        return True
    from PIL import Image
    try:
        with Image.open(io.BytesIO(payload)) as served, Image.open(io.BytesIO(image.image_bytes)) as expected:
            if getattr(served, "n_frames", 1) != 1:   # 첫 프레임만 같은 APNG 는 다른 그림
                return False
            return served.size == expected.size and served.convert("RGBA").tobytes() == expected.convert("RGBA").tobytes()
    except (OSError, ValueError, Image.DecompressionBombError):
        return False


def compare_rehosted_images(dom: list[dict], images: list[BodyImage]) -> str:
    """재호스팅된 이미지를 순서대로 내려받아 기대 그림과 대조 — ok | fetch(내려받기 실패) | content(다른 그림).
    크기 대조만으로는 같은 크기의 이전 그래프가 통과한다(Codex r2·r3 P1)."""
    for item, image in zip(dom, images):
        try:
            payload = fetch_rehosted_image(str(item.get("src") or ""))
        except Exception:
            return "fetch"
        if not _same_picture(payload, image):
            return "content"
    return "ok"


def verify_body_images(page, images: list[BodyImage], timeout_ms: int = 30000, poll_ms: int = 500) -> str:
    """재호스팅 완료까지 기다린 뒤 내용까지 대조한다. 제한 시간 안에 ok 가 아니면 마지막 상태를 돌려준다
    (호출자가 ERR 로 멈춤)."""
    waited = 0
    while True:
        dom = read_editor_images(page)
        state = editor_image_state(dom, images)
        if state == "ok":
            return compare_rehosted_images(dom, images)
        if waited >= timeout_ms:
            return state
        page.wait_for_timeout(poll_ms)
        waited += poll_ms


def _source_state(images: list[BodyImage]) -> str:
    for image in images:
        try:
            payload = fetch_source_image(image.url)
        except Exception:
            return "fetch"
        if not _same_picture(payload, image):
            return "content"
    return "ok"


def wait_for_source_images(images: list[BodyImage], timeout_s: float = 180, poll_s: float = 10,
                           sleep=time.sleep) -> str:
    """붙여넣기 전 — 편집기는 원고 URL 을 받아 재호스팅하므로 사이트가 sidecar 의 그림을 서빙해야 한다.
    Pages 배포 지연을 기다린 뒤 ok | fetch(제한 시간 안에 못 받음) | content(다른 그림) 를 돌려준다."""
    waited = 0.0
    while True:
        state = _source_state(images)
        if state == "ok" or waited >= timeout_s:
            return state
        sleep(poll_s)
        waited += poll_s
