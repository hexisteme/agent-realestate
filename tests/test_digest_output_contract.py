from pathlib import Path

import pytest

from blog.run_daily import write_digest_outputs
from blog.tistory_contract import parse_helper


DIGEST = {
    "title": "서울 아파트 합성 다이제스트",
    "tags": "서울아파트,실거래가",
    "tistory_html": "<p>합성 티스토리 본문</p>",
    "site_html": "<!DOCTYPE html><html><head><title>서울 아파트 합성 다이제스트</title></head><body>합성 사이트 본문</body></html>",
    "summary": "합성 요약",
}


def test_write_digest_outputs_round_trips_and_keeps_dated_latest_identical(tmp_path):
    outputs = write_digest_outputs(DIGEST, "2026-09-26", str(tmp_path))
    assert parse_helper(outputs["draft"]) == {
        "title": DIGEST["title"], "tags": DIGEST["tags"], "body": DIGEST["tistory_html"],
    }
    assert Path(outputs["dated"]).read_text(encoding="utf-8") == DIGEST["site_html"]
    assert Path(outputs["latest"]).read_text(encoding="utf-8") == DIGEST["site_html"]


@pytest.mark.parametrize("bad", [
    {},
    {**DIGEST, "title": ""},
    {**DIGEST, "extra": "unexpected"},
])
def test_incomplete_or_ambiguous_digest_is_rejected_before_writing(tmp_path, bad):
    with pytest.raises(ValueError, match="contract"):
        write_digest_outputs(bad, "2026-09-26", str(tmp_path))
    assert not list(tmp_path.rglob("*"))


@pytest.mark.parametrize("site_html", [
    "<!DOCTYPE html><html><body>missing head</body></html>",
    "<!DOCTYPE html><html><head></head><head></head><body>duplicate head</body></html>",
])
def test_invalid_metadata_injection_point_leaves_no_orphan_card(tmp_path, site_html):
    digest = {**DIGEST, "site_html": site_html}
    dataset = {"generated": "2026-09-27", "data_asof": "2026-09-27", "complexes": []}
    with pytest.raises(ValueError, match="unique metadata injection point"):
        write_digest_outputs(digest, "2026-09-27", str(tmp_path), ds=dataset)
    assert not list(tmp_path.rglob("*"))


DATASET = {"generated": "2026-09-27", "data_asof": "2026-09-27", "complexes": []}


@pytest.fixture
def rendered_cards(monkeypatch):
    from blog import thumbnail_card

    render = thumbnail_card.build_daily_card
    cards = []

    def render_card(*args, **kwargs):
        card = render(*args, **kwargs)
        cards.append((Path(card.path), Path(card.path).read_bytes()))
        return card

    monkeypatch.setattr(thumbnail_card, "build_daily_card", render_card)
    return cards


@pytest.mark.parametrize("rejection", ["helper", "media"])
def test_rejected_digest_removes_only_newly_rendered_png(tmp_path, rendered_cards, rejection):
    from blog.tistory_contract import DraftContractError
    from blog.tistory_media import MediaContractError

    digest, dataset = dict(DIGEST), dict(DATASET)
    if rejection == "helper":
        digest["title"] = " padded synthetic title "
        error = DraftContractError
    else:
        dataset["data_asof"] = "2026-09-28"
        error = MediaContractError
    with pytest.raises(error):
        write_digest_outputs(digest, "2026-09-27", str(tmp_path), ds=dataset)

    assert len(rendered_cards) == 1
    assert rendered_cards[0][1].startswith(b"\x89PNG\r\n\x1a\n")
    assert not list(tmp_path.rglob("*.png"))
    assert not list(tmp_path.glob(".daily-digest-*"))


def test_rejected_helper_preserves_preexisting_png_even_when_name_collides(tmp_path, rendered_cards):
    from blog.thumbnail_card import build_daily_card
    from blog.tistory_contract import DraftContractError

    probe = build_daily_card(DATASET, tmp_path / "probe", today="2026-09-27")
    output = tmp_path / "output"
    existing = output / "images" / Path(probe.path).name
    existing.parent.mkdir(parents=True)
    original = b"preexisting user-owned PNG bytes"
    existing.write_bytes(original)
    bad = {**DIGEST, "title": " padded synthetic title "}

    with pytest.raises(DraftContractError):
        write_digest_outputs(bad, "2026-09-27", str(output), ds=DATASET)

    assert existing.read_bytes() == original
    assert list(output.rglob("*.png")) == [existing]
    assert not list(output.glob(".daily-digest-*"))


def test_rejected_helper_preserves_preexisting_accepted_png(tmp_path):
    from blog.tistory_contract import DraftContractError
    from blog.tistory_media import read_media_manifest

    accepted = write_digest_outputs(DIGEST, "2026-09-27", str(tmp_path), ds=DATASET)
    image = Path(accepted["image"])
    original = image.read_bytes()
    helper = Path(accepted["draft"]).read_bytes()
    bad = {**DIGEST, "title": " padded synthetic title "}

    with pytest.raises(DraftContractError):
        write_digest_outputs(bad, "2026-09-27", str(tmp_path), ds=DATASET)

    assert image.read_bytes() == original
    assert Path(accepted["draft"]).read_bytes() == helper
    assert read_media_manifest(accepted["draft"], parse_helper(accepted["draft"]), required=True).image_bytes == original


def test_accepted_digest_keeps_card_and_final_media_paths(tmp_path, rendered_cards):
    from blog.tistory_media import read_media_manifest

    outputs = write_digest_outputs(DIGEST, "2026-09-27", str(tmp_path), ds=DATASET)
    payload = parse_helper(outputs["draft"])
    verified = read_media_manifest(outputs["draft"], payload, required=True)

    assert Path(outputs["image"]).read_bytes() == rendered_cards[0][1]
    assert verified.image_path == outputs["image"]
    assert verified.image_bytes == rendered_cards[0][1]
    assert Path(outputs["dated"]).read_bytes() == Path(outputs["latest"]).read_bytes()
    assert all(Path(path).is_file() and ".daily-digest-" not in path for path in outputs.values())
    assert all(".daily-digest-" not in Path(outputs[key]).read_text() for key in ("draft", "dated", "latest", "media"))
    assert not list(tmp_path.glob(".daily-digest-*"))


def test_relative_digest_output_directory_keeps_existing_path_contract(tmp_path, monkeypatch):
    from blog.tistory_media import read_media_manifest

    monkeypatch.chdir(tmp_path)
    outputs = write_digest_outputs(DIGEST, "2026-09-27", "output", ds=DATASET)
    assert outputs["draft"] == "output/tistory/2026-09-27-tistory-draft.html"
    assert outputs["dated"] == "output/daily/2026-09-27.html"
    assert Path(outputs["image"]).is_absolute()
    assert read_media_manifest(outputs["draft"], parse_helper(outputs["draft"]), required=True).image_path == outputs["image"]


def test_digest_output_root_symlink_keeps_resolved_card_path(tmp_path):
    from blog.tistory_media import read_media_manifest

    actual = tmp_path / "actual"
    actual.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(actual, target_is_directory=True)
    outputs = write_digest_outputs(DIGEST, "2026-09-27", str(alias), ds=DATASET)
    assert Path(outputs["draft"]).parent == alias / "tistory"
    assert Path(outputs["image"]).parent == actual / "images"
    assert read_media_manifest(outputs["draft"], parse_helper(outputs["draft"]), required=True).image_path == outputs["image"]


def test_digest_images_directory_alias_returns_canonical_card_parent(tmp_path):
    from blog.tistory_media import read_media_manifest

    output = tmp_path / "output"
    output.mkdir()
    images = tmp_path / "card-storage"
    images.mkdir()
    (output / "images").symlink_to(images, target_is_directory=True)
    outputs = write_digest_outputs(DIGEST, "2026-09-27", str(output), ds=DATASET)
    verified = read_media_manifest(outputs["draft"], parse_helper(outputs["draft"]), required=True)

    assert verified.image_bytes == Path(outputs["image"]).read_bytes()
    assert Path(outputs["image"]).parent == images
    assert verified.image_path == outputs["image"]
    assert not list(output.glob(".daily-digest-*"))


def test_relocated_tistory_parent_rejects_before_any_promotion(tmp_path, rendered_cards):
    output = tmp_path / "output"
    output.mkdir()
    relocated = tmp_path / "relocated-tistory"
    relocated.mkdir()
    helper = relocated / "2026-09-27-tistory-draft.html"
    original = b"preexisting relocated helper"
    helper.write_bytes(original)
    (output / "tistory").symlink_to(relocated, target_is_directory=True)

    with pytest.raises(ValueError, match="daily helper image layout"):
        write_digest_outputs(DIGEST, "2026-09-27", str(output), ds=DATASET)

    assert helper.read_bytes() == original
    assert len(rendered_cards) == 1
    assert not list(output.rglob("*.png"))
    assert not list(output.glob(".daily-digest-*"))


@pytest.mark.parametrize("relative", [
    "tistory/2026-09-27-tistory-draft.html", "daily/2026-09-27.html",
    "daily/latest.html", "tistory/2026-09-27-tistory-draft.media.json",
])
def test_digest_promotion_failure_does_not_leave_new_png(tmp_path, monkeypatch, rendered_cards, relative):
    from blog import run_daily

    replace = run_daily.os.replace

    def fail_final_helper(source, destination):
        if Path(destination) == tmp_path / relative:
            raise OSError("synthetic digest promotion failure")
        return replace(source, destination)

    monkeypatch.setattr(run_daily.os, "replace", fail_final_helper)
    with pytest.raises(OSError, match="synthetic digest promotion failure"):
        write_digest_outputs(DIGEST, "2026-09-27", str(tmp_path), ds=DATASET)

    assert len(rendered_cards) == 1
    assert not list(tmp_path.rglob("*.png"))
    assert not list(tmp_path.glob(".daily-digest-*"))


def test_existing_card_collision_rejects_before_replacing_accepted_helper(tmp_path):
    outputs = write_digest_outputs(DIGEST, "2026-09-27", str(tmp_path), ds=DATASET)
    image = Path(outputs["image"])
    image.write_bytes(b"preexisting different bytes")
    original = {key: Path(path).read_bytes() for key, path in outputs.items()}
    with pytest.raises(ValueError, match="existing daily image"):
        write_digest_outputs(DIGEST, "2026-09-27", str(tmp_path), ds=DATASET)
    assert {key: Path(path).read_bytes() for key, path in outputs.items()} == original
    assert not list(tmp_path.glob(".daily-digest-*"))


def test_identical_existing_card_is_reused_without_replacement(tmp_path):
    first = write_digest_outputs(DIGEST, "2026-09-27", str(tmp_path), ds=DATASET)
    image = Path(first["image"])
    original = image.read_bytes(), image.stat().st_ino, image.stat().st_mtime_ns
    second = write_digest_outputs(DIGEST, "2026-09-27", str(tmp_path), ds=DATASET)
    assert second == first
    assert (image.read_bytes(), image.stat().st_ino, image.stat().st_mtime_ns) == original


def test_digest_link_failure_leaves_no_new_png(tmp_path, monkeypatch):
    from blog import run_daily
    from blog.tistory_media import MediaContractError, read_media_manifest

    def fail_link(source, destination):
        raise OSError("synthetic image link failure")

    monkeypatch.setattr(run_daily.os, "link", fail_link)
    with pytest.raises(OSError, match="synthetic image link failure"):
        write_digest_outputs(DIGEST, "2026-09-27", str(tmp_path), ds=DATASET)
    assert not list(tmp_path.rglob("*.png"))
    assert not list(tmp_path.glob(".daily-digest-*"))
    helper = tmp_path / "tistory/2026-09-27-tistory-draft.html"
    with pytest.raises(MediaContractError):
        read_media_manifest(helper, parse_helper(helper), required=True)


def test_concurrent_card_creation_never_overwrites_other_writer(tmp_path, monkeypatch):
    from blog import run_daily

    link = run_daily.os.link
    existing = []

    def competing_link(source, destination):
        image = Path(destination)
        content = b"concurrent writer bytes"
        image.write_bytes(content)
        existing.append((image, content, image.stat().st_ino))
        return link(source, destination)

    monkeypatch.setattr(run_daily.os, "link", competing_link)
    with pytest.raises(ValueError, match="existing daily image"):
        write_digest_outputs(DIGEST, "2026-09-27", str(tmp_path), ds=DATASET)
    image, content, inode = existing[0]
    assert image.read_bytes() == content and image.stat().st_ino == inode
    assert not list(tmp_path.glob(".daily-digest-*"))
