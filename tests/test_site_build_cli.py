"""The site build entry point must expose frozen-cohort controls before writing."""
import os
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

import blog.build_site as build_site
from blog.tistory_contract import DraftPayload, write_helper
from blog.tistory_media import write_media_manifest


def _invoke(tmp_path, *args):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return subprocess.run([sys.executable, "-m", "blog.build_site", *args],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)


def test_help_exposes_probe_and_cohort_without_building(tmp_path):
    result = _invoke(tmp_path, "--help")
    assert result.returncode == 0
    assert "--probe" in result.stdout and "--cohort" in result.stdout
    assert not list(tmp_path.iterdir())


def test_invalid_date_exits_before_any_site_is_written(tmp_path):
    result = _invoke(tmp_path, "--date", "2026-02-30")
    assert result.returncode == 2
    assert "YYYY-MM-DD" in result.stderr
    assert not list(tmp_path.iterdir())


def _tree_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
            for path in root.rglob("*")}


@pytest.fixture
def build_tree(tmp_path, monkeypatch):
    source, site = tmp_path / "source", tmp_path / "site"
    for root in (source, site):
        (root / "posts").mkdir(parents=True)
        (root / "posts/2026-11-01-old.html").write_text("new source" if root == source else "keep existing post")
        (root / "dataset.json").write_text(json.dumps({"complexes": [], "generated": "2026-11-02"}))
    (site / "complex").mkdir()
    (site / "complex/stale.html").write_text("keep stale owner until controls validate")
    helper = source / "tistory/2026-11-02-tistory-draft.html"
    data = {"title": "서울 관측", "body": "<p>실거래</p>", "tags": "서울"}
    write_helper(helper, DraftPayload(**data), "2026-11-02")
    image = source / "images/daily-2026-11-02-0123456789abcdef.png"
    image.parent.mkdir()
    Image.new("RGB", (1200, 630), "white").save(image)
    write_media_manifest(helper, data, SimpleNamespace(path=image, sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                                                      alt="서울 관측", observed_date="2026-11-02", data_asof="2026-11-01"))
    monkeypatch.setattr(build_site, "SRC", str(source))
    monkeypatch.setattr(build_site, "SITE", str(site))
    monkeypatch.setattr(build_site, "BASE_URL", "https://example.test/realestate")
    monkeypatch.delenv("BLOG_ACQUISITION_PROBE", raising=False)
    monkeypatch.delenv("BLOG_ACQUISITION_COHORT", raising=False)
    return source, site


@pytest.mark.parametrize("control", ["probe", "cohort"])
@pytest.mark.parametrize("kind", ["missing", "malformed", "incompatible"])
def test_invalid_build_controls_preserve_existing_output(build_tree, tmp_path, control, kind):
    source, site = build_tree
    control_path = tmp_path / "control.json"
    if kind != "missing":
        control_path.write_text("{" if kind == "malformed" else json.dumps({"schema": "wrong"}))
    before = _tree_bytes(site)
    with pytest.raises((ValueError, KeyError, FileNotFoundError)):
        build_site.build("2026-11-02", str(tmp_path / "no-molit.json"), **{f"{control}_path": control_path})
    assert _tree_bytes(site) == before
    assert not (source / "snapshots").exists()


def _frozen_build_cohort(build_tree, tmp_path):
    from test_seo_geo_completion import _probe, _write_page
    from test_build_site_prune import _row
    from blog.acquisition_probe import build_treatment_overlay, freeze_probe_cohort
    probe, intents, _ = _probe(tmp_path)
    source, site = build_tree
    # Use the real owner pages in the build's already-existing site.
    for intent in intents:
        marker = f'<section data-acquisition-probe="{probe["probe_id"]}">실거래 직답</section>' if intent.intent_id in build_treatment_overlay(probe) else ""
        _write_page(site, intent, marker=marker)
    rows = [_row(intent.entity_ids[0], intent.entity_ids[1]) for intent in intents if intent.entity_type == "complex"]
    dataset = {"complexes": rows, "generated": "2026-11-02", "data_asof": "2026-11-01", "count": len(rows)}
    (source / "dataset.json").write_text(json.dumps(dataset))
    path = tmp_path / "cohort.json"
    cohort = freeze_probe_cohort(probe, intents, site, build_site.BASE_URL, path, frozen_date="2026-11-01")
    return path, cohort, dataset, probe


@pytest.mark.parametrize("kind", ["snapshot", "removed_owner", "base_url", "frozen_intent", "probe_mismatch"])
def test_invalid_frozen_contract_preserves_owners_before_prune(build_tree, tmp_path, kind):
    cohort_path, cohort, dataset, probe = _frozen_build_cohort(build_tree, tmp_path)
    source, site = build_tree
    if kind == "snapshot":
        Path(cohort["owners"][-1]["snapshot_path"]).write_text("changed snapshot")
    elif kind == "removed_owner":
        removed = cohort["owners"][0]["intent"]["entity_ids"]
        dataset["complexes"] = [row for row in dataset["complexes"] if [row["gu"], row["name"]] != list(removed)]
        (source / "dataset.json").write_text(json.dumps(dataset))
    elif kind == "base_url":
        cohort["base_url"] = "https://other.test/realestate"
    elif kind == "frozen_intent":
        cohort["owners"][-1]["intent"]["locale"] = "en-US"
    else:
        probe["probe_id"] = "different-valid-probe"
    cohort_path.write_text(json.dumps(cohort))
    probe_path = tmp_path / "probe.json"
    probe_path.write_text(json.dumps(probe))
    before = _tree_bytes(site)
    with pytest.raises(ValueError):
        build_site.build("2026-11-02", str(tmp_path / "no-molit.json"), cohort_path=cohort_path,
                         probe_path=probe_path if kind == "probe_mismatch" else None)
    assert _tree_bytes(site) == before
    assert not (source / "snapshots").exists()


@pytest.mark.parametrize("with_probe", [False, True])
def test_valid_build_restores_frozen_payloads_and_registry(build_tree, tmp_path, with_probe):
    from blog.acquisition_probe import validate_probe_cohort
    from blog.search_intent import read_intent_registry
    cohort_path, cohort, _, probe = _frozen_build_cohort(build_tree, tmp_path)
    _, site = build_tree
    probe_path = tmp_path / "probe.json"
    probe_path.write_text(json.dumps(probe))
    build_site.build("2026-11-02", str(tmp_path / "no-molit.json"), cohort_path=cohort_path,
                     probe_path=probe_path if with_probe else None)
    assert validate_probe_cohort(cohort, site, build_site.BASE_URL)["status"] == "PASS"
    registry = {intent.intent_id: intent for intent in read_intent_registry(site / "search-intents.jsonl")}
    for owner in cohort["owners"]:
        assert registry[owner["intent"]["intent_id"]].observed_at == "2026-11-01"
