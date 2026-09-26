"""Bind an optional generated representative card to one exact draft payload."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
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


def write_media_manifest(helper, data, card) -> str:
    """Generated sidecar; the PNG must be inside the sibling images directory."""
    helper = Path(helper).resolve()
    image = Path(card.path).resolve()
    image_dir = (helper.parent.parent / "images").resolve()
    if image.parent != image_dir or not re.fullmatch(r"daily-[0-9-]+-[0-9a-f]{16}\.png", image.name):
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
    source = re.sub(r'<meta name="tistory-representative-sha256" content="[0-9a-f]{64}">\n?', "", source)
    updated = source.replace("</head>", marker + "\n</head>", 1)
    fd, temporary_helper = tempfile.mkstemp(prefix=f".{helper.name}.", dir=helper.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(updated)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_helper, helper)
    finally:
        if os.path.exists(temporary_helper):
            os.unlink(temporary_helper)
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


def _read_media_manifest(helper, data, *, required=False) -> DraftMedia | None:
    helper = Path(helper).resolve()
    sidecar = media_path(helper)
    head = helper.read_text(encoding="utf-8").split("</head>", 1)[0]
    declared = re.findall(r'<meta name="tistory-representative-sha256" content="([0-9a-f]{64})">', head)
    if len(declared) > 1 or ("tistory-representative-sha256" in head and not declared):
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
                or not re.fullmatch(r"\.\./images/daily-[0-9-]+-[0-9a-f]{16}\.png", record["image"])
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
        if (record["data_asof"] > record["observed_date"]
                or not re.fullmatch(
                    rf"\.\./images/daily-{re.escape(record['observed_date'])}-[0-9a-f]{{16}}\.png",
                    record["image"])):
            raise MediaContractError()
        daily_date = re.fullmatch(r"([0-9]{4}-[0-9]{2}-[0-9]{2})-tistory-draft\.html", helper.name)
        if daily_date and daily_date[1] != record["observed_date"]:
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
        return DraftMedia(str(image), record["sha256"], record["alt"], content)
    except MediaContractError:
        raise
    except Exception:
        raise MediaContractError() from None
