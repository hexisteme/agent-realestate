"""티스토리 kind 별 원고·마커(2026-09-07 주간결산/월간결산 2편째) — daily 는 종전 경로 그대로, periodic 은 접미사.
daily 최신 원고 해소가 periodic 원고를 집어 이중 발행하는 경로를 차단하는지까지."""
from __future__ import annotations

from blog.tistory_draft import write_digest_draft
from blog.tistory_publish_pw import _resolve_draft, marker_paths


def test_marker_paths_daily_unchanged_and_kind_suffixed(tmp_path):
    root = str(tmp_path)
    assert marker_paths(root)[0] == f"{root}/.last-tistory-published"
    assert marker_paths(root, "periodic") == (f"{root}/.last-tistory-published-periodic",
                                              f"{root}/.last-tistory-attempted-periodic",
                                              f"{root}/.last-tistory-alerted-periodic")


def test_resolve_draft_daily_ignores_kind_drafts(tmp_path):
    t = tmp_path / "report/blog/tistory"; t.mkdir(parents=True)
    (t / "2026-09-12-tistory-draft.html").write_text("x")
    (t / "2026-09-13-periodic-tistory-draft.html").write_text("y")
    assert _resolve_draft(str(tmp_path), None, "daily").endswith("2026-09-12-tistory-draft.html")
    assert _resolve_draft(str(tmp_path), None, "periodic").endswith("2026-09-13-periodic-tistory-draft.html")
    assert _resolve_draft(str(tmp_path), "2026-09-13", "periodic").endswith("2026-09-13-periodic-tistory-draft.html")
    assert _resolve_draft(str(tmp_path), "2026-09-13", "daily") is None
    assert _resolve_draft(str(tmp_path), None, "nothing") is None


def test_write_digest_draft_kind_path(tmp_path):
    p = write_digest_draft({"title": "t", "tags": "a", "tistory_html": "<p>b</p>"}, "2026-09-13", str(tmp_path), kind="periodic")
    assert p.endswith("/tistory/2026-09-13-periodic-tistory-draft.html")
    q = write_digest_draft({"title": "t", "tags": "a", "tistory_html": "<p>b</p>"}, "2026-09-13", str(tmp_path))
    assert q.endswith("/tistory/2026-09-13-tistory-draft.html")
