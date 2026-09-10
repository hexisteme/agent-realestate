"""날짜·원고 종류별 발행 기록. 손상된 기록을 미발행으로 취급하지 않는다."""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


class DeliveryError(Exception):
    """원문·시크릿을 포함하지 않는 발행 기록 오류."""

    def __init__(self) -> None:
        super().__init__("발행 기록을 확인하거나 저장할 수 없습니다")


_FIELDS = {"date", "kind", "state", "title", "digest", "url", "updated_at"}


def delivery_path(outroot: str, date: str, kind: str) -> Path:
    """프로젝트 아래의 날짜별 기록 경로. 비정규 날짜·경로 삽입은 거부."""
    try:
        if not isinstance(date, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date):
            raise DeliveryError()
        if datetime.date.fromisoformat(date).isoformat() != date or kind not in ("daily", "periodic"):
            raise DeliveryError()
        return Path(outroot) / "report/blog/publications" / f"{date}-{kind}.json"
    except (TypeError, ValueError):
        raise DeliveryError() from None


def payload_digest(data: dict) -> str:
    """실제 발행 제목·본문·태그만 결박하며 부가 메타데이터는 무시."""
    if not isinstance(data, dict) or any(not isinstance(data.get(k), str) for k in ("title", "body", "tags")):
        raise DeliveryError()
    try:
        canonical = json.dumps({k: data[k] for k in ("title", "body", "tags")},
                               ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                               allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise DeliveryError() from None
    return hashlib.sha256(canonical).hexdigest()


def _unique_fields(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeliveryError()
        result[key] = value
    return result


def _validate_record(record: dict, date: str, kind: str) -> None:
    if not isinstance(record, dict) or set(record) != _FIELDS:
        raise DeliveryError()
    if record["date"] != date or record["kind"] != kind or record["state"] not in ("ATTEMPTED", "PUBLISHED"):
        raise DeliveryError()
    if any(not isinstance(record[k], str) for k in _FIELDS):
        raise DeliveryError()
    if not re.fullmatch(r"[0-9a-f]{64}", record["digest"]):
        raise DeliveryError()
    try:
        timestamp = datetime.datetime.fromisoformat(record["updated_at"])
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise DeliveryError()
    except (TypeError, ValueError):
        raise DeliveryError() from None


def read_delivery(outroot: str, date: str, kind: str) -> dict | None:
    """파일 부재만 None. 잘림·형식 오류·읽기 실패는 재발행 대신 오류."""
    path = delivery_path(outroot, date, kind)
    try:
        record = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)
    except FileNotFoundError:
        if path.is_symlink():
            raise DeliveryError() from None
        return None
    except (OSError, ValueError, UnicodeError):
        raise DeliveryError() from None
    _validate_record(record, date, kind)
    return record


def write_delivery(outroot: str, date: str, kind: str, *, state: str, data: dict, url: str = "") -> dict:
    """본문·태그 원문 없이 기록. fsync 후 같은 디렉터리에서 원자적 교체."""
    path = delivery_path(outroot, date, kind)
    digest = payload_digest(data)
    record = {"date": date, "kind": kind, "state": state, "title": data["title"],
              "digest": digest, "url": url,
              "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    _validate_record(record, date, kind)
    temporary = None
    try:
        encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".delivery-", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        # rename만 성공하고 디렉터리 갱신이 유실되는 재부팅 경로도 막는다.
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (OSError, TypeError, ValueError, UnicodeError):
        raise DeliveryError() from None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError:
                raise DeliveryError() from None
    return record
