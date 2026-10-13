"""Bind an optional generated representative card to one exact draft payload."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
import os
from pathlib import Path
import re
import tempfile

from blog.tistory_delivery import payload_digest


class MediaContractError(ValueError):
    """Fixed error text, never including file contents or secret paths."""

    def __init__(self):
        super().__init__("representative image contract invalid")


@dataclass(frozen=True)
class DraftMedia:
    image_path: str
    sha256: str
    alt: str
    image_bytes: bytes = field(repr=False)


def media_path(helper: str | Path) -> Path:
    return Path(helper).with_suffix(".media.json")


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MediaContractError()
        result[key] = value
    return result


_MARKER_RE = r'<meta name="tistory-representative-sha256" content="[0-9a-f]{64}">\n?'


def _card_prefix(helper: Path, data) -> str:
    """원고가 받는 대표 그림 접두사. 일간 원고는 daily, 결산 원고는 주간·월간이 같은 파일명이라 태그(주간결산/월간결산)로
    weekly·monthly 를 가른다 — 태그가 둘 다거나 없으면 둘 다 허용(외부 리뷰 U12, R6)."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-periodic-tistory-draft\.html", helper.name):
        return "daily"
    tags = {tag.strip() for tag in str((data or {}).get("tags") or "").split(",")}
    kinds = [kind for kind, label in (("weekly", "주간결산"), ("monthly", "월간결산")) if label in tags]
    return kinds[0] if len(kinds) == 1 else "(?:weekly|monthly)"


def _write_atomic(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def discard_media_manifest(helper) -> None:
    """대표 그림 없이 발행할 원고에서 이전 실행의 표지(meta)와 sidecar 를 지운다. 남으면 새 원고와 어긋나
    발행이 ERR:representative image contract invalid 로 막힌다(R6)."""
    helper = Path(helper).resolve()
    sidecar = media_path(helper)
    if sidecar.exists() or sidecar.is_symlink():
        sidecar.unlink()
    source = helper.read_text(encoding="utf-8")
    cleaned = re.sub(_MARKER_RE, "", source)
    if cleaned != source:
        _write_atomic(helper, cleaned)


def write_media_manifest(helper, data, card) -> str:
    """Generated sidecar; the PNG must be inside the sibling images directory."""
    helper = Path(helper).resolve()
    image = Path(card.path).resolve()
    image_dir = (helper.parent.parent / "images").resolve()
    if image.parent != image_dir or not re.fullmatch(r"(?:daily|weekly|monthly)-[0-9-]+-[0-9a-f]{16}\.png", image.name):
        raise MediaContractError()
    # 종류·날짜가 원고와 안 맞는 그림은 원고를 건드리기 전에 거부한다(쓰고 나서 검증 실패하면 반쯤 쓴 계약이 남는다).
    if not re.fullmatch(rf"{_card_prefix(helper, data)}-{re.escape(str(card.observed_date))}-[0-9a-f]{{16}}\.png", image.name):
        raise MediaContractError()
    record = {
        "schema": "TistoryMedia/v1", "draft": helper.name,
        "payload_sha256": payload_digest(data), "image": f"../images/{image.name}",
        "sha256": card.sha256, "width": 1200, "height": 630, "alt": card.alt,
        "observed_date": card.observed_date, "data_asof": card.data_asof,
    }
    destination = media_path(helper)
    marker = f'<meta name="tistory-representative-sha256" content="{card.sha256}">'
    source = helper.read_text(encoding="utf-8")
    if source.count("</head>") != 1:
        raise MediaContractError()
    source = re.sub(_MARKER_RE, "", source)
    _write_atomic(helper, source.replace("</head>", marker + "\n</head>", 1))
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
    read_media_manifest(helper, data, required=True)
    return str(destination)


def read_media_manifest(helper, data, *, required=False) -> DraftMedia | None:
    """Verify once and retain the exact PNG bytes for upload, eliminating path TOCTOU."""
    try:
        return _read_media_manifest(helper, data, required=required)
    except MediaContractError:
        raise
    except Exception:
        raise MediaContractError() from None


class _RepresentativeMarkers(HTMLParser):
    """Interpret marker attributes once, including HTML character references."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.digests: list[str] = []
        self.in_head = False

    def handle_starttag(self, tag, attrs):
        if tag == "head":
            self.in_head = True
        if tag != "meta" or not self.in_head:
            return
        names = [value for key, value in attrs if key == "name"]
        if not any(value and value.lower() == "tistory-representative-sha256" for value in names):
            return
        contents = [value for key, value in attrs if key == "content"]
        if (len(names) != 1 or len(contents) != 1 or not isinstance(contents[0], str)
                or not re.fullmatch(r"[0-9a-f]{64}", contents[0])):
            raise MediaContractError()
        self.digests.append(contents[0])

    def handle_endtag(self, tag):
        if tag == "head":
            self.in_head = False


def _read_media_manifest(helper, data, *, required=False) -> DraftMedia | None:
    helper = Path(helper).resolve()
    sidecar = media_path(helper)
    markers = _RepresentativeMarkers()
    markers.feed(helper.read_text(encoding="utf-8"))
    markers.close()
    declared = markers.digests
    if len(declared) > 1:
        raise MediaContractError()
    if not sidecar.exists():
        if required or declared:
            raise MediaContractError()
        return None  # Existing legacy/periodic drafts remain compatible.
    try:
        if sidecar.stat().st_size > 16_384:
            raise MediaContractError()
        record = json.loads(sidecar.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
        fields = {"schema", "draft", "payload_sha256", "image", "sha256", "width", "height",
                  "alt", "observed_date", "data_asof"}
        if not isinstance(record, dict) or set(record) != fields:
            raise MediaContractError()
        if (record["schema"] != "TistoryMedia/v1" or record["draft"] != helper.name
                or record["payload_sha256"] != payload_digest(data)
                or type(record["width"]) is not int or type(record["height"]) is not int
                or record["width"] != 1200 or record["height"] != 630
                or not isinstance(record["alt"], str) or not record["alt"].strip()
                or len(record["alt"]) > 2_000
                or not isinstance(record["image"], str)
                or not re.fullmatch(r"\.\./images/(?:daily|weekly|monthly)-[0-9-]+-[0-9a-f]{16}\.png", record["image"])
                or not isinstance(record["sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])
                or (declared and declared[0] != record["sha256"])):
            raise MediaContractError()
        for key in ("observed_date", "data_asof"):
            value = record[key]
            if (not isinstance(value, str)
                    or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value)
                    or date.fromisoformat(value).isoformat() != value):
                raise MediaContractError()
        # 그림 종류는 원고 종류와 맞아야 한다 — 일간 원고는 daily-, 결산(periodic) 원고는 weekly-·monthly-(외부 리뷰 U12).
        daily_date = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2})-tistory-draft\.html", helper.name)
        periodic_date = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2})-periodic-tistory-draft\.html", helper.name)
        prefix = _card_prefix(helper, data)
        if (record["data_asof"] > record["observed_date"]
                or not re.fullmatch(
                    rf"\.\./images/{prefix}-{re.escape(record['observed_date'])}-[0-9a-f]{{16}}\.png",
                    record["image"])):
            raise MediaContractError()
        helper_date = daily_date or periodic_date
        if helper_date and helper_date[1] != record["observed_date"]:
            raise MediaContractError()
        image = (helper.parent / record["image"]).resolve()
        if image.parent != (helper.parent.parent / "images").resolve():
            raise MediaContractError()
        if image.stat().st_size > 500_000:
            raise MediaContractError()
        content = image.read_bytes()
        if hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise MediaContractError()
        from PIL import Image
        with Image.open(BytesIO(content)) as png:
            if png.format != "PNG" or png.size != (1200, 630):
                raise MediaContractError()
            png.verify()
        with Image.open(BytesIO(content)) as png:
            png.load()
        return DraftMedia(str(image), record["sha256"], record["alt"], content)
    except MediaContractError:
        raise
    except Exception:
        raise MediaContractError() from None
