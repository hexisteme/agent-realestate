"""Review packets retain observed dates and never write to publication inputs."""
from __future__ import annotations

from datetime import date
import hashlib
import html
import json
from pathlib import Path
import tempfile

import pytest

from blog import review_periodic as rp


@pytest.fixture
def review_case(monkeypatch):
    report = rp.PROJECT_ROOT / "report"
    report.mkdir(exist_ok=True)
    monkeypatch.setattr(rp, "_today", lambda: date(2026, 9, 8))
    with tempfile.TemporaryDirectory(prefix="test-period-review-", dir=report) as folder:
        root = Path(folder)
        snapshots = root / "snapshots"
        snapshots.mkdir()
        base = [_row(f"단지{i}", 10.0) for i in range(6)]
        (snapshots / "dataset-2026-09-06.json").write_text(json.dumps(_ds(base, "2026-09-06")), encoding="utf-8")
        dataset = root / "dataset.json"
        dataset.write_text(json.dumps(_ds(base + [_row("신규", 30.0)], "2026-09-08")), encoding="utf-8")
        yield {"today": "2026-09-13", "dataset": str(dataset), "snapshots": str(snapshots),
               "out": str(root / "review")}


def _row(name, price):
    return {"gu": "강남", "name": name, "area_m2": 84.0, "product_type": "아파트", "molit_n": 15,
            "molit_recent_eok": price, "molit_pos_52w": 50, "price_segment": "10~15억"}


def _ds(rows, generated):
    return {"generated": generated, "data_asof": generated, "count": len(rows), "complexes": rows}


def test_future_preview_uses_real_dates_preserves_hashes_and_blocks_analytics(review_case):
    result = rp.review_periodic(**review_case)
    assert result["status"] == "PREVIEW_ONLY" and result["human_review"] == "pending"
    assert (result["generated"], result["data_asof"]) == ("2026-09-08", "2026-09-08")
    assert (result["n_cur"], result["n_pairs"], result["n_valid"]) == (7, 6, 6)
    packet = json.loads(Path(result["out"], "review.json").read_text())
    assert packet["delta"]["today"] == "2026-09-08"
    assert packet["delta"]["seoul_median_cur"] == packet["delta"]["seoul_median_base"] == 10
    for source in result["inputs"]:
        assert hashlib.sha256(Path(source["path"]).read_bytes()).hexdigest() == source["sha256"]
    for name in ("review-site.html", "review-tistory.html"):
        text = Path(result["out"], name).read_text()
        assert "PREVIEW_ONLY" in text and 'content="noindex,nofollow"' in text
        assert "2026-09-13" in text and "실제 데이터 생성일 2026-09-08" in text
        assert "script-src 'none'" in text and "connect-src 'none'" in text
        assert "googletagmanager" not in text and "gtag(" not in text and "<script" not in text
    assert not Path(review_case["snapshots"], "dataset-2026-09-13.json").exists()
    assert not Path(result["out"], "tistory").exists()


def test_observed_publication_date_is_ready_for_review_without_approval(review_case, monkeypatch):
    monkeypatch.setattr(rp, "_today", lambda: date(2026, 9, 13))
    path = Path(review_case["dataset"])
    ds = json.loads(path.read_text())
    ds.update(generated="2026-09-13", data_asof="2026-09-13")
    path.write_text(json.dumps(ds))
    result = rp.review_periodic(**review_case)
    assert result["status"] == "READY_FOR_REVIEW" and result["human_review"] == "pending"
    assert "승인 아님" in Path(result["out"], "REVIEW.md").read_text()


def test_operational_existing_and_symlink_output_paths_are_rejected(review_case):
    root = rp.PROJECT_ROOT
    existing = Path(review_case["out"]).parent
    link = existing / "to-operational"
    link.symlink_to(root / "report" / "blog", target_is_directory=True)
    for destination in (root / "report", root / "report/blog/review-forbidden", root / "site/review-forbidden",
                        root / "review-forbidden", existing, link / "review-forbidden",
                        Path(review_case["snapshots"]) / "review-forbidden"):
        with pytest.raises(ValueError):
            rp.review_periodic(**{**review_case, "out": str(destination)})
    assert not Path(review_case["out"]).exists()


def test_future_dataset_or_mislabeled_snapshot_is_rejected_without_output(review_case):
    dataset = Path(review_case["dataset"])
    original = dataset.read_bytes()
    ds = json.loads(original)
    ds.update(generated="2026-09-13", data_asof="2026-09-13")
    dataset.write_text(json.dumps(ds))
    with pytest.raises(ValueError, match="future"):
        rp.review_periodic(**review_case)
    dataset.write_bytes(original)
    base = Path(review_case["snapshots"], "dataset-2026-09-06.json")
    ds = json.loads(base.read_text())
    ds.update(generated="2026-09-07", data_asof="2026-09-07")
    base.write_text(json.dumps(ds))
    with pytest.raises(ValueError, match="filename"):
        rp.review_periodic(**review_case)
    assert not Path(review_case["out"]).exists()


def test_monthly_preview_preserves_macro_sources_note_and_real_snapshot_rows(review_case):
    fixture = rp.PROJECT_ROOT / "tests/fixtures/macro/snapshot_2026-09-07.json"
    result = rp.review_periodic(**{**review_case, "today": "2026-09-27", "macro_snapshot": str(fixture)})
    packet = json.loads(Path(result["out"], "review.json").read_text())
    delta = packet["delta"]
    assert delta["weekly_rows"] == [] and delta["weekly"] is None
    assert delta["today"] == "2026-09-08" and delta["macro_context"]["asof"] == "2026-09-07"
    assert result["tistory_bytes"] <= rp.pd.TISTORY_BUDGET
    assert any("실제 비교 기간은 2일" in w for w in result["warnings"])
    for name in ("review-site.html", "review-tistory.html"):
        text = Path(result["out"], name).read_text()
        assert delta["macro_context"]["note_line"] in rp._visible(text)
        assert rp.pd.be.DISCLAIMER in rp._visible(text)
        for source in delta["macro_context"]["sources"]:
            assert source["source"] in rp._visible(text) and html.escape(source["url"]) in text


def test_repeated_input_read_rejects_mutation_and_retains_first_hash(review_case):
    path = Path(review_case["dataset"])
    inputs = {}
    ds = rp._read(path, inputs)
    first_hash = inputs[str(path.resolve())]["sha256"]
    assert rp._read(path, inputs) == ds
    ds["complexes"][0]["molit_recent_eok"] = 20.0
    path.write_text(json.dumps(ds), encoding="utf-8")
    with pytest.raises(rp.ReviewError, match="changed between reads"):
        rp._read(path, inputs)
    assert inputs[str(path.resolve())]["sha256"] == first_hash
    assert not Path(review_case["out"]).exists()


@pytest.mark.parametrize("same_content", [True, False])
def test_same_date_monthly_dataset_and_snapshot_require_equal_contents(review_case, monkeypatch, same_content):
    monkeypatch.setattr(rp, "_today", lambda: date(2026, 9, 27))
    dataset = Path(review_case["dataset"])
    ds = json.loads(dataset.read_text())
    ds.update(generated="2026-09-27", data_asof="2026-09-27")
    dataset.write_text(json.dumps(ds), encoding="utf-8")
    if not same_content:
        ds["complexes"][0]["molit_recent_eok"] = 20.0
    snapshot = Path(review_case["snapshots"], "dataset-2026-09-27.json")
    snapshot.write_text(json.dumps(ds, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    assert snapshot.read_bytes() != dataset.read_bytes()  # JSON formatting differences are allowed.
    args = {**review_case, "today": "2026-09-27"}
    if not same_content:
        with pytest.raises(rp.ReviewError, match="same-date dataset and snapshot contents differ"):
            rp.review_periodic(**args)
        assert not Path(review_case["out"]).exists()
    else:
        result = rp.review_periodic(**args)
        assert result["status"] == "READY_FOR_REVIEW"
        packet = json.loads(Path(result["out"], "review.json").read_text())
        assert packet["delta"]["weekly_rows"][-1]["n"] == packet["delta"]["n_cur"]


@pytest.mark.parametrize("fault", ["None", "NaN", "Infinity", "budget", "disclosure", "macro_note", "macro_source"])
def test_render_regressions_fail_closed_without_output(review_case, monkeypatch, fault):
    render = rp.pd.render_period_post

    def broken(delta):
        post = render(delta)
        if fault in {"None", "NaN", "Infinity"}:
            post["tistory_html"] += f"<p>{fault}원</p>"
        elif fault == "budget":
            post["tistory_html"] += "x" * rp.pd.TISTORY_BUDGET
        elif fault == "disclosure":
            post["tistory_html"] = post["tistory_html"].replace(rp.pd.be.DISCLAIMER, "")
        elif fault == "macro_note":
            post["tistory_html"] = post["tistory_html"].replace(delta["macro_context"]["note_line"], "")
        else:
            source = delta["macro_context"]["sources"][0]
            post["tistory_html"] = post["tistory_html"].replace(html.escape(source["url"]), "https://missing.invalid")
        return post

    monkeypatch.setattr(rp.pd, "render_period_post", broken)
    fixture = rp.PROJECT_ROOT / "tests/fixtures/macro/snapshot_2026-09-07.json"
    with pytest.raises(ValueError):
        rp.review_periodic(**{**review_case, "today": "2026-09-27", "macro_snapshot": str(fixture)})
    args = {**review_case, "today": "2026-09-27", "macro_snapshot": str(fixture)}
    assert rp.main([part for k, v in args.items() for part in ("--" + k.replace("_", "-"), v)]) == 1
    assert not Path(review_case["out"]).exists()
