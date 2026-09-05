"""build_site 데이터셋 축소 가드(2026-09-05) — 11gu 기본값 데이터셋이 25gu 사이트 위에 조립되는 사고 차단."""
import json
import pytest

import blog.build_site as bs


def _ds(path, n):
    path.write_text(json.dumps({"complexes": [{"complex_no": str(i)} for i in range(n)]}), encoding="utf-8")
    return str(path)


def test_shrink_guard_blocks_large_drop(tmp_path, monkeypatch):
    monkeypatch.delenv("RE_ALLOW_SHRINK", raising=False)
    with pytest.raises(SystemExit) as ei:
        bs.assert_dataset_not_shrunk(_ds(tmp_path / "new.json", 117), _ds(tmp_path / "old.json", 643))
    assert "643 → 117" in str(ei.value)


def test_shrink_guard_allows_normal_variation_and_first_build(tmp_path, monkeypatch):
    monkeypatch.delenv("RE_ALLOW_SHRINK", raising=False)
    bs.assert_dataset_not_shrunk(_ds(tmp_path / "new.json", 600), _ds(tmp_path / "old.json", 643))
    bs.assert_dataset_not_shrunk(_ds(tmp_path / "new2.json", 5), str(tmp_path / "missing.json"))   # 첫 조립: 기존 파일 없음


def test_shrink_guard_explicit_override_and_unparsable_old(tmp_path, monkeypatch):
    monkeypatch.setenv("RE_ALLOW_SHRINK", "1")
    bs.assert_dataset_not_shrunk(_ds(tmp_path / "new.json", 1), _ds(tmp_path / "old.json", 643))
    monkeypatch.delenv("RE_ALLOW_SHRINK")
    bad = tmp_path / "bad.json"; bad.write_text("{not json", encoding="utf-8")
    bs.assert_dataset_not_shrunk(_ds(tmp_path / "new2.json", 1), str(bad))
