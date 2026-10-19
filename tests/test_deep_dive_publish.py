"""격일 연재 「한 단지의 시간」 무인 발행 게이트 — 2026-10-19 사용자 결정(회차별 사람 승인 → 자동 발행).

게이트는 브라우저 없이 결정론으로 판정한다. 초안 세션이 남긴 판정 파일(render-check·article-claims)과
원장 상태가 계약을 지키지 않으면 발행하지 않는다."""
import json
from pathlib import Path

import pytest
from PIL import Image

from blog import deep_dive_publish as dd

D = "2026-10-21"
CHARTS = ("history.png", "recent.png")

PREVIEW = """<!doctype html><html><head><title>t</title></head><body><main>
<h1>까치마을 34.44㎡, 15억 원 안팎으로 올라선 최근 거래</h1>
<p>본문 한 줄과 <a href="https://example.gov/a">출처</a>.</p>
<h2>절 제목</h2>
<div class="tw"><table><thead><tr><th>머리</th><th>둘</th></tr></thead>
<tbody><tr><td>값</td><td>2</td></tr></tbody></table></div>
<p><a href="history.png"><img src="history.png" alt="연도별 매매·전세 중위"></a></p>
<p><a href="recent.png"><img src="recent.png" alt="최근 24개월 개별 계약"></a></p>
<blockquote>독자의 최종 판단</blockquote>
</main></body></html>
"""


def _render(**over):
    r = {"render_pass": True, "numbers_pass": True, "n_number_checks": 42,
         "banned_phrases": [], "unformatted_placeholders": [],
         "render": [{"viewport": v, "overflow": False, "page_errors": [],
                     "images": [{"src": c, "loaded": True, "alt": True} for c in CHARTS]}
                    for v in ("desktop", "mobile")]}
    r.update(over)
    return r


def _claims(n=42, **over):
    return [{"label": f"c{i}", "json_value": str(i), "ok": True, **over} for i in range(n)]


def _draft(tmp_path, *, day=D, state="DRAFT_READY", episodes=None, preview=PREVIEW, render=...,
           claims=..., readme="DRAFT_READY\n\n# 검토본\n", charts=CHARTS, name="까치마을"):
    root = Path(tmp_path)
    base = root / dd.DRAFT_ROOT / day
    base.mkdir(parents=True)
    for fn, body in (("preview.html", preview), ("README.md", readme)):
        if body is not None:
            (base / fn).write_text(body, encoding="utf-8")
    for fn, obj in (("render-check.json", render), ("article-claims.json", claims)):
        obj = (_render() if fn.startswith("render") else _claims()) if obj is ... else obj
        if obj is not None:
            (base / fn).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    for c in charts:
        Image.new("RGB", (640, 360), "white").save(base / c)
    eps = episodes if episodes is not None else [
        {"edition": 3, "asof": "2026-10-19", "name": "수서까치마을", "state": "PUBLISHED"},
        {"edition": 4, "asof": day, "name": name, "district": "강남구", "apt_seq": "11680-314",
         "area_m2": 34.44, "state": state, "draft_dir": str(base)}]
    led = {"series": "한 단지의 시간", "cadence_days": 2, "publication": dd.PUBLICATION_MODE, "episodes": eps}
    (root / dd.LEDGER).write_text(json.dumps(led, ensure_ascii=False), encoding="utf-8")
    return str(root)


# ── 통과 ──────────────────────────────────────────────────────────────────

def test_gate_passes_on_complete_draft(tmp_path):
    assert dd.gate(_draft(tmp_path), D) == []


def test_charts_follow_preview_order(tmp_path):
    assert dd.charts(_draft(tmp_path), D) == list(CHARTS)


def test_tags_name_the_series_and_the_complex(tmp_path):
    root = _draft(tmp_path, name="수서 까치마을")
    tags = dd.tags(dd.episode(dd.load_ledger(root), D))
    assert tags.split(",")[:2] == ["한단지의시간", "수서까치마을"]
    assert "서울아파트" in tags and " " not in tags


def test_build_body_keeps_text_and_drops_every_local_reference(tmp_path):
    root = _draft(tmp_path)
    title, body, reference, alts = dd.build_body(root, D)
    assert title.startswith("까치마을 34.44㎡")
    assert title not in body and "<h1" not in body
    assert body.count("CHART_PLACEHOLDER_") == 2 and alts == ["연도별 매매·전세 중위", "최근 24개월 개별 계약"]
    assert ".png" not in body and "file:" not in body and "<style" not in body
    assert "<th" not in body and "<strong" in body and "<thead" not in body
    assert "margin:20px 0;" in body and "https://example.gov/a" in body
    assert "절 제목" in reference.get_text() and reference.find("h1") is None


# ── 원장 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", ["PUBLISHED", "PUBLISH_ATTEMPTED", "HOLD_NO_CASE", "DRAFT"])
def test_gate_blocks_any_state_but_draft_ready(tmp_path, state):
    reasons = dd.gate(_draft(tmp_path, state=state), D)
    assert reasons and any(state in r for r in reasons)


def test_gate_blocks_when_the_episode_is_missing_or_doubled(tmp_path):
    only_old = [{"edition": 3, "asof": "2026-10-19", "state": "PUBLISHED"}]
    assert dd.gate(_draft(tmp_path, episodes=only_old), D)
    twice = [{"edition": 4, "asof": D, "state": "DRAFT_READY"}, {"edition": 5, "asof": D, "state": "DRAFT_READY"}]
    assert dd.gate(_draft(tmp_path / "b", episodes=twice), D)


def test_gate_blocks_when_draft_dir_in_ledger_is_another_place(tmp_path):
    eps = [{"edition": 4, "asof": D, "name": "까치마을", "state": "DRAFT_READY",
            "draft_dir": "/tmp/somewhere-else"}]
    assert dd.gate(_draft(tmp_path, episodes=eps), D)


def test_gate_blocks_without_a_ledger(tmp_path):
    root = _draft(tmp_path)
    (Path(root) / dd.LEDGER).unlink()
    assert dd.gate(root, D)


# ── 초안 세션 산출물 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("readme", [None, "HOLD_NO_CASE\n후보 없음\n", "FAILED\n", "\nDRAFT_READY\n"])
def test_gate_blocks_unless_readme_declares_draft_ready(tmp_path, readme):
    assert dd.gate(_draft(tmp_path, readme=readme), D)


@pytest.mark.parametrize("over", [
    {"render_pass": False}, {"numbers_pass": False}, {"n_number_checks": 0},
    {"banned_phrases": ["적정가"]}, {"unformatted_placeholders": ["{price}"]},
    {"render": []},
    {"render": [{"viewport": "desktop", "overflow": False, "page_errors": [], "images": []}]},
])
def test_gate_blocks_on_declared_render_or_number_failure(tmp_path, over):
    assert dd.gate(_draft(tmp_path, render=_render(**over)), D)


@pytest.mark.parametrize("bad", [
    {"overflow": True}, {"page_errors": ["TypeError"]},
    {"images": [{"src": "history.png", "loaded": False, "alt": True}]},
    {"images": [{"src": "history.png", "loaded": True, "alt": False}]},
])
def test_gate_blocks_on_a_failed_viewport(tmp_path, bad):
    r = _render()
    r["render"][1].update(bad)
    assert dd.gate(_draft(tmp_path, render=r), D)


def test_gate_blocks_without_render_check(tmp_path):
    assert dd.gate(_draft(tmp_path, render=None), D)


@pytest.mark.parametrize("claims", [None, [], _claims(ok=False), [{"label": "c", "json_value": "1"}], {"n": 1}])
def test_gate_blocks_unless_every_number_check_passed(tmp_path, claims):
    assert dd.gate(_draft(tmp_path, claims=claims), D)


def test_gate_blocks_when_claim_count_and_render_check_disagree(tmp_path):
    assert dd.gate(_draft(tmp_path, claims=_claims(41)), D)


# ── 검토본 ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("preview", [
    None,
    "<main><p>제목 없음</p></main>",
    "<main><h1>제목</h1><p>그림 없음</p></main>",
    '<main><h1>제목</h1><img src="https://cdn.example/x.png" alt="원격"></main>',
    '<main><h1>제목</h1><img src="../other/history.png" alt="바깥"></main>',
    '<main><h1>제목</h1><img src="history.png" alt=""></main>',
])
def test_gate_blocks_a_preview_that_breaks_the_contract(tmp_path, preview):
    assert dd.gate(_draft(tmp_path, preview=preview), D)


def test_gate_blocks_when_a_chart_file_is_missing(tmp_path):
    assert dd.gate(_draft(tmp_path, charts=("history.png",)), D)


# ── 발행 진입 ─────────────────────────────────────────────────────────────

def test_run_refuses_before_opening_a_browser_when_the_gate_blocks(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "_publish_locked", lambda *a, **k: pytest.fail("브라우저를 열었다"))
    out = dd.run(_draft(tmp_path, state="PUBLISHED"), D, publish=True)
    assert out.startswith("REFUSE:") and "PUBLISHED" in out


def test_run_records_publication_in_the_ledger_without_human_approval(tmp_path):
    root = _draft(tmp_path)
    dd.write_ledger(root, D, state="PUBLISHED", published_url="https://floker.tistory.com/131")
    ep = dd.episode(dd.load_ledger(root), D)
    assert ep["state"] == "PUBLISHED" and ep["published_url"].endswith("/131") and ep["updated_at"]
    assert "human_approval" not in ep
    assert dd.load_ledger(root)["episodes"][0]["asof"] == "2026-10-19"
