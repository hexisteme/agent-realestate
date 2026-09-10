"""합성 기록만 사용. 실제 발행 경로·네트워크·브라우저 호출 없음."""
import hashlib
import json
import os

import pytest

from blog import tistory_delivery as delivery


DATA = {"title": "합성 발행 제목", "body": "<p>SYNTHETIC BODY ONLY</p>", "tags": "SYNTHETIC TAGS"}
DATE = "2026-09-10"


def test_digest_canonical_and_only_publication_payload():
    expected = hashlib.sha256(json.dumps(DATA, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()
    assert delivery.payload_digest(DATA) == expected
    assert delivery.payload_digest({"extra": "ignored", **dict(reversed(list(DATA.items())))}) == expected
    for field in DATA:
        assert delivery.payload_digest({**DATA, field: DATA[field] + " changed"}) != expected


@pytest.mark.parametrize("date,kind", [("20260910", "daily"), ("2026-9-10", "daily"),
                                     ("2026-02-30", "daily"), ("2026-09-10\n", "daily"),
                                     ("../2026-09-10", "daily"), (DATE, "../daily"),
                                     (DATE, "weekly"), (None, "daily")])
def test_identity_rejected_before_path_creation(tmp_path, date, kind):
    with pytest.raises(delivery.DeliveryError):
        delivery.write_delivery(str(tmp_path), date, kind, state="ATTEMPTED", data=DATA)
    assert not list(tmp_path.iterdir())


def test_records_are_separate_by_date_and_kind_without_body_or_tags(tmp_path):
    assert delivery.read_delivery(str(tmp_path), DATE, "daily") is None
    for day, kind in [(DATE, "daily"), (DATE, "periodic"), ("2026-09-11", "daily")]:
        result = delivery.write_delivery(str(tmp_path), day, kind, state="ATTEMPTED", data=DATA)
        path = delivery.delivery_path(str(tmp_path), day, kind)
        assert path == tmp_path / "report/blog/publications" / f"{day}-{kind}.json"
        assert delivery.read_delivery(str(tmp_path), day, kind) == result
        assert set(result) == {"date", "kind", "state", "title", "digest", "url", "updated_at"}
        assert DATA["body"] not in path.read_text() and DATA["tags"] not in path.read_text()
        assert path.stat().st_mode & 0o777 == 0o600
    url = "https://example.invalid/123"
    final = delivery.write_delivery(str(tmp_path), DATE, "daily", state="PUBLISHED", data=DATA, url=url)
    assert delivery.read_delivery(str(tmp_path), DATE, "daily") == final
    assert final["state"] == "PUBLISHED" and final["url"] == url
    assert not list((tmp_path / "report/blog/publications").glob(".delivery-*"))


@pytest.mark.parametrize("raw", ["{", "[]", "null", '{"date":"SECRET_SENTINEL"}',
                                 '{"date":"2026-09-10","date":"2026-09-10"}', b"\xff"])
def test_truncated_or_malformed_never_means_not_published(tmp_path, raw):
    path = delivery.delivery_path(str(tmp_path), DATE, "daily")
    path.parent.mkdir(parents=True)
    path.write_bytes(raw if isinstance(raw, bytes) else raw.encode())
    with pytest.raises(delivery.DeliveryError) as caught:
        delivery.read_delivery(str(tmp_path), DATE, "daily")
    assert str(caught.value) == "발행 기록을 확인하거나 저장할 수 없습니다"
    assert "SECRET_SENTINEL" not in str(caught.value)


@pytest.mark.parametrize("field,value", [("date", "2026-09-11"), ("kind", "periodic"),
                                       ("state", "SUCCESS"), ("digest", "abcd"),
                                       ("title", None), ("url", []),
                                       ("updated_at", "2026-09-10T12:00:00"),
                                       ("body", "UNEXPECTED PRIVATE BODY")])
def test_mismatched_or_invalid_receipt_rejected(tmp_path, field, value):
    record = delivery.write_delivery(str(tmp_path), DATE, "daily", state="PUBLISHED", data=DATA)
    record[field] = value
    delivery.delivery_path(str(tmp_path), DATE, "daily").write_text(json.dumps(record))
    with pytest.raises(delivery.DeliveryError):
        delivery.read_delivery(str(tmp_path), DATE, "daily")


def test_read_io_failure_and_dangling_symlink_are_fail_closed(tmp_path, monkeypatch):
    path = delivery.delivery_path(str(tmp_path), DATE, "daily")
    path.parent.mkdir(parents=True)
    path.symlink_to(tmp_path / "missing-record")
    with pytest.raises(delivery.DeliveryError):
        delivery.read_delivery(str(tmp_path), DATE, "daily")
    path.unlink()
    path.mkdir()
    with pytest.raises(delivery.DeliveryError):
        delivery.read_delivery(str(tmp_path), DATE, "daily")


@pytest.mark.parametrize("stage", ["fsync", "replace"])
def test_atomic_failure_preserves_previous_receipt_and_cleans_temp(tmp_path, monkeypatch, stage):
    old = delivery.write_delivery(str(tmp_path), DATE, "daily", state="ATTEMPTED", data=DATA)
    def fail(*args):
        raise OSError("SECRET_SENTINEL")
    monkeypatch.setattr(delivery.os, stage, fail)
    with pytest.raises(delivery.DeliveryError) as caught:
        delivery.write_delivery(str(tmp_path), DATE, "daily", state="PUBLISHED", data=DATA)
    assert "SECRET_SENTINEL" not in str(caught.value)
    assert delivery.read_delivery(str(tmp_path), DATE, "daily") == old
    assert not list(delivery.delivery_path(str(tmp_path), DATE, "daily").parent.glob(".delivery-*"))


def test_fsync_precedes_replace_then_directory_sync(tmp_path, monkeypatch):
    events = []
    original_fsync, original_replace = os.fsync, os.replace
    def sync(fd):
        events.append("fsync")
        original_fsync(fd)
    def replace(source, target):
        assert events == ["fsync"]
        # 최종 경로에 부분 JSON은 나타나지 않는다.
        assert not target.exists()
        events.append("replace")
        original_replace(source, target)
    monkeypatch.setattr(delivery.os, "fsync", sync)
    monkeypatch.setattr(delivery.os, "replace", replace)
    delivery.write_delivery(str(tmp_path), DATE, "daily", state="ATTEMPTED", data=DATA)
    assert events == ["fsync", "replace", "fsync"]


@pytest.mark.parametrize("data", [None, {}, {**DATA, "body": 123}, {**DATA, "tags": ["tag"]}])
def test_malformed_payload_rejected_without_writing(tmp_path, data):
    with pytest.raises(delivery.DeliveryError):
        delivery.write_delivery(str(tmp_path), DATE, "daily", state="ATTEMPTED", data=data)
    assert not list(tmp_path.iterdir())
