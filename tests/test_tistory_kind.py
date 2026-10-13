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


def test_category_candidates_route_periodic_posts_to_their_own_category_with_daily_fallback():
    """외부 리뷰 U13 — 주간·월간 결산이 일간 '오늘의 변화'에 섞이지 않게 별도 카테고리로. 새 카테고리가 블로그에
    없으면(CAT_NOTFOUND) 분류 없음으로 새지 않게 일간 카테고리로 물러선다. 과거 글은 옮기지 않는다(소급 금지)."""
    from blog import tistory_publish_pw as tp
    assert tp.category_candidates("daily") == (tp.CATEGORY_MATCH,)
    assert tp.category_candidates("periodic") == (tp.PERIODIC_CATEGORY_MATCH, tp.CATEGORY_MATCH)
    assert tp.PERIODIC_CATEGORY_MATCH != tp.CATEGORY_MATCH
    assert tp.CATEGORY_MATCH not in tp.PERIODIC_CATEGORY_MATCH  # 부분일치 선택이 일간 항목을 먼저 집지 않게


def test_category_pick_prefers_the_visible_exact_menu_item(required_browser):
    """R6(Codex U13-1) — 부분일치가 메뉴 앞의 안내 문구·숨은 항목을 집지 않게 보이는 메뉴 항목의 정확한 이름을 먼저 고른다."""
    from blog import tistory_publish_pw as tp
    page = required_browser.new_page()
    try:
        for kind, expected, target in (("periodic", "주간·월간 결산", "real"), ("daily", "오늘의 변화", "daily")):
            page.set_content(
                '<span id="decoy">주간·월간 결산 안내</span><span id="decoy2">오늘의 변화 보기</span>'
                '<div class="mce-menu-item" id="hidden" style="display:none">주간·월간 결산</div>'
                '<div class="mce-menu-item" id="daily"><span class="mce-text">오늘의 변화</span></div>'
                '<div class="mce-menu-item" id="real"><span class="mce-text">주간·월간 결산</span></div>'
                '<script>window.hit = null; document.querySelectorAll("[id]").forEach(e => e.addEventListener("click",'
                ' ev => { if (!window.hit) window.hit = ev.currentTarget.id; }));</script>')
            assert page.evaluate(tp.CATEGORY_PICK_JS, list(tp.category_candidates(kind))) == expected
            assert page.evaluate("window.hit") == target, kind
    finally:
        page.close()
