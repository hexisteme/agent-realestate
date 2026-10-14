"""연재 원고(upsize·deposit·watch) 티스토리 발행 경로(2026-10-14 승인) — 일간·결산과 마커·기록·카테고리 분리."""
import pytest

from blog import tistory_delivery as td
from blog import tistory_publish_pw as pw

SERIES = ("upsize", "deposit", "watch")


def test_series_kinds_are_publication_kinds():
    assert pw.SERIES_KINDS == SERIES
    assert set(SERIES) < set(td.PUBLICATION_KINDS) and {"daily", "periodic"} < set(td.PUBLICATION_KINDS)


@pytest.mark.parametrize("kind", SERIES)
def test_series_category_never_falls_into_daily(kind):
    cands = pw.category_candidates(kind)
    assert cands == (pw.SERIES_CATEGORY_MATCH, "부동산") and pw.CATEGORY_MATCH not in cands
    assert pw.SERIES_CATEGORY_MATCH == "서울(아파트)"


@pytest.mark.parametrize("kind", SERIES)
def test_series_markers_and_delivery_records_are_separate(tmp_path, kind):
    daily = pw.marker_paths(str(tmp_path), "daily")
    series = pw.marker_paths(str(tmp_path), kind)
    assert not set(daily) & set(series) and all(p.endswith(f"-{kind}") for p in series)
    assert td.delivery_path(str(tmp_path), "2026-10-13", kind).name == f"2026-10-13-{kind}.json"


def test_series_draft_resolves_only_by_explicit_name(tmp_path):
    d = tmp_path / "report/blog/tistory"; d.mkdir(parents=True)
    for nm in ("2026-10-13-tistory-draft.html", "2026-10-13-upsize-tistory-draft.html",
               "2026-10-13-deposit-tistory-draft.html"):
        (d / nm).write_text("x", encoding="utf-8")
    assert pw._resolve_draft(str(tmp_path), "2026-10-13", "upsize").endswith("2026-10-13-upsize-tistory-draft.html")
    assert pw._resolve_draft(str(tmp_path), "2026-10-13", "daily").endswith("2026-10-13-tistory-draft.html")
    assert pw._latest_draft_for_kind(str(tmp_path), "daily").endswith("2026-10-13-tistory-draft.html")


def test_series_publish_requires_explicit_date(tmp_path):
    assert pw.publish(str(tmp_path), "publish", None, kind="upsize") == "ERR:series_requires_date"


def test_series_publish_refuses_held_edition(tmp_path):
    d = tmp_path / "report/blog/tistory"; d.mkdir(parents=True)
    (d / "2026-11-10-upsize-tistory-draft.html").write_text("x", encoding="utf-8")
    h = tmp_path / "report/blog/series/upsize"; h.mkdir(parents=True)
    (h / "2026-11-10.hold.json").write_text("{}", encoding="utf-8")
    assert pw.publish(str(tmp_path), "publish", "2026-11-10", kind="upsize") == "ERR:series_held"


def test_series_publish_requires_judged_edition(tmp_path):
    d = tmp_path / "report/blog/tistory"; d.mkdir(parents=True)
    (d / "2026-11-10-upsize-tistory-draft.html").write_text("x", encoding="utf-8")
    assert pw.publish(str(tmp_path), "publish", "2026-11-10", kind="upsize") == "ERR:series_unjudged"


def test_series_design_review_is_required(tmp_path):
    draft = tmp_path / "2026-11-10-upsize-tistory-draft.html"
    draft.write_text("x", encoding="utf-8")
    assert pw._gate_design_review(str(draft), None, draft.name, required=True).startswith("DESIGN_AWAIT_HUMAN:")
    assert pw._gate_design_review(str(draft), None, draft.name) is None


def test_unknown_kind_still_rejected(tmp_path):
    assert pw.publish(str(tmp_path), "publish", "2026-10-13", kind="insight") == "ERR:invalid publication mode/kind"
    with pytest.raises(td.DeliveryError):
        td.delivery_path(str(tmp_path), "2026-10-13", "insight")


def test_cli_accepts_series_kind_but_not_pending(monkeypatch, capsys):
    monkeypatch.setattr(pw, "publish", lambda *a, **k: "PUBLISHED")
    monkeypatch.setattr("sys.argv", ["x", "--mode", "publish", "--kind", "watch", "--date", "2026-10-13", "--no-notify"])
    assert pw.main() == 0
    monkeypatch.setattr("sys.argv", ["x", "--mode", "publish", "--kind", "watch", "--pending"])
    with pytest.raises(SystemExit):
        pw.main()


@pytest.mark.parametrize("kind", SERIES)
def test_series_stops_when_no_category_picked(kind):
    """G6 Codex: 후보가 메뉴에 없으면 '분류 없음'으로 공개 발행되던 경로 — 연재는 멈춘다."""
    assert pw.category_outcome(kind, None) == ("CAT_NOTFOUND", True)
    assert pw.category_outcome(kind, "서울(아파트)", "서울(아파트)") == ("CAT", False)
    assert pw.category_outcome(kind, "부동산", "부동산") == ("CAT_FALLBACK:부동산", False)


def test_daily_category_outcome_unchanged():
    assert pw.category_outcome("daily", None) == ("CAT_NOTFOUND", False)
    assert pw.category_outcome("periodic", "오늘의 변화") == ("CAT_FALLBACK:오늘의 변화", False)


@pytest.mark.parametrize("kind", SERIES)
def test_series_stops_when_editor_shows_other_category(kind):
    """R3 Codex: 숨은 항목 클릭은 후보 이름을 돌려줘도 실제 선택은 '분류 없음' — 에디터 표시값으로 확인한다."""
    assert pw.category_outcome(kind, "부동산", "분류 없음") == ("CAT_MISMATCH:분류 없음", True)
    assert pw.category_outcome(kind, "서울(아파트)", " - 서울(아파트) ") == ("CAT", False)
    assert pw.category_outcome(kind, "서울(아파트)", None) == ("CAT_MISMATCH:", True)


def test_daily_ignores_editor_category_text():
    assert pw.category_outcome("daily", "오늘의 변화", "분류 없음") == ("CAT", False)
