"""Real helper/PNG media binding regressions."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from blog.tistory_contract import DraftPayload, write_helper
from blog.tistory_media import MediaContractError, media_path, read_media_manifest, write_media_manifest


@pytest.fixture
def media(tmp_path):
    helper = tmp_path / "tistory/2026-09-26-tistory-draft.html"
    data = {"title": "서울 관측", "body": "<p>표본</p>", "tags": "서울"}
    write_helper(helper, DraftPayload(**data), "2026-09-26")
    image = tmp_path / "images/daily-2026-09-26-0123456789abcdef.png"
    image.parent.mkdir()
    Image.new("RGB", (1200, 630), "white").save(image)
    card = SimpleNamespace(path=image, sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                           alt="서울 실거래 표본", observed_date="2026-09-26", data_asof="2026-09-25")
    write_media_manifest(helper, data, card)
    return helper, data, image


def change_record(helper, **changes):
    sidecar = media_path(helper)
    record = json.loads(sidecar.read_text())
    record.update(changes)
    sidecar.write_text(json.dumps(record))


@pytest.mark.parametrize("changes", [
    {"observed_date": "2026-02-30"}, {"data_asof": "20260925"},
    {"data_asof": "2026-09-27"}, {"observed_date": "2026-09-25"},
    {"width": 1200.0}, {"height": 630.0}, {"width": True},
])
def test_rejects_invalid_dates_and_dimension_types(media, changes):
    helper, data, _ = media
    change_record(helper, **changes)
    with pytest.raises(MediaContractError):
        read_media_manifest(helper, data)


def test_helper_read_failure_is_fixed_contract_error(media):
    helper, data, _ = media
    helper.write_bytes(b"\xff")
    with pytest.raises(MediaContractError):
        read_media_manifest(helper, data)


def test_daily_helper_date_must_match_card(media):
    helper, data, _ = media
    target = helper.with_name("2026-09-27-tistory-draft.html")
    helper.rename(target)
    media_path(helper).rename(media_path(target))
    change_record(target, draft=target.name)
    with pytest.raises(MediaContractError):
        read_media_manifest(target, data)


def test_verified_bytes_survive_image_replacement(media):
    helper, data, image = media
    expected = image.read_bytes()
    verified = read_media_manifest(helper, data)
    image.write_bytes(b"replaced")
    assert verified.image_bytes == expected


def test_marker_requires_sidecar_and_legacy_does_not(media):
    helper, data, _ = media
    media_path(helper).unlink()
    with pytest.raises(MediaContractError):
        read_media_manifest(helper, data)
    write_helper(helper, DraftPayload(**data), "2026-09-26")
    assert read_media_manifest(helper, data) is None


@pytest.mark.parametrize("changes", [{"payload_sha256": "0" * 64}, {"sha256": "0" * 64},
                                      {"alt": " "}, {"image": "../../outside.png"}])
def test_corrupted_binding_is_rejected(media, changes):
    helper, data, _ = media
    change_record(helper, **changes)
    with pytest.raises(MediaContractError):
        read_media_manifest(helper, data)


def test_duplicate_sidecar_key_is_rejected(media):
    helper, data, _ = media
    sidecar = media_path(helper)
    sidecar.write_text(sidecar.read_text().rstrip()[:-1] + ',"width":1200}')
    with pytest.raises(MediaContractError):
        read_media_manifest(helper, data)


def test_external_image_symlink_is_rejected(media, tmp_path):
    helper, data, image = media
    outside = tmp_path / "outside.png"
    image.rename(outside)
    image.symlink_to(outside)
    with pytest.raises(MediaContractError):
        read_media_manifest(helper, data)


@pytest.mark.parametrize("kind", ["sha", "format", "dimensions", "size"])
def test_actual_image_corruption_is_rejected(media, kind):
    helper, data, image = media
    if kind == "sha":
        image.write_bytes(b"corrupt PNG")
    else:
        if kind == "format":
            Image.new("RGB", (1200, 630)).save(image, format="JPEG")
        elif kind == "dimensions":
            Image.new("RGB", (1200, 629)).save(image)
        else:
            image.write_bytes(image.read_bytes() + b"x" * 500_000)
        digest = hashlib.sha256(image.read_bytes()).hexdigest()
        record = json.loads(media_path(helper).read_text())
        helper.write_text(helper.read_text().replace(record["sha256"], digest))
        change_record(helper, sha256=digest)
    with pytest.raises(MediaContractError):
        read_media_manifest(helper, data)


def test_future_observation_is_allowed_when_all_dates_agree(media):
    helper, data, image = media
    future = helper.with_name("2030-01-01-tistory-draft.html")
    helper.rename(future)
    media_path(helper).rename(media_path(future))
    target = image.with_name("daily-2030-01-01-0123456789abcdef.png")
    image.rename(target)
    change_record(future, draft=future.name, image=f"../images/{target.name}",
                  observed_date="2030-01-01", data_asof="2030-01-01")
    assert read_media_manifest(future, data).image_bytes == target.read_bytes()


def test_periodic_legacy_helper_does_not_require_media(media):
    helper, data, _ = media
    periodic = helper.with_name("2026-09-26-periodic-tistory-draft.html")
    write_helper(periodic, DraftPayload(**data), "2026-09-26")
    assert read_media_manifest(periodic, data) is None
