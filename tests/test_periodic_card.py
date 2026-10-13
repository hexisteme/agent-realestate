"""외부 리뷰(2026-10-13) U12 — 주간·월간 결산도 글자 없는 대표 그림을 단다(일간과 도형·색으로 구분).

일간: 청록 둥근 사각 타일(구별 상승 비율) / 주간: 호박색 원 / 월간: 보라 마름모. 칸 = 서울 25개 구 지리 배치,
채움 = 기준 스냅샷 대비 같은 단지 짝의 구 중위 변화(+ 따뜻한 색, − 푸른색, 0·비교 없음은 테두리만).
"""
import re
from pathlib import Path

import pytest

from blog import thumbnail_card as card
from blog import tistory_media as tm
from blog.tistory_draft import write_digest_draft
from blog.tistory_publish import _parse_helper

SAFE = (312, 0, 888, 300)


@pytest.fixture
def korean_font():
    pytest.importorskip("PIL")
    try:
        return card._resolve_font()
    except FileNotFoundError:
        pytest.skip("Korean font unavailable on this host")


def _delta(kind="weekly", deltas=None):
    deltas = {"강남": 1.5, "노원": -0.8, "마포": 0.0} if deltas is None else deltas
    return {"kind": kind, "today": "2026-10-11", "asof": "2026-10-11", "base_date": "2026-10-04",
            "gus": [{"gu": g, "gu_median_delta_pct": v, "delta_median_pct": v, "n_valid": 5} for g, v in deltas.items()]}


def test_periodic_card_is_textless_kind_specific_and_inside_the_cover_crop(korean_font, tmp_path):
    rendered = card.draw_periodic_card(card.prepare_periodic_card(_delta()), korean_font)
    assert rendered.text_boxes == [] and len(rendered.tile_boxes) == 25
    for left, top, right, bottom in rendered.tile_boxes:
        assert SAFE[0] <= left and right <= SAFE[2] and bottom <= SAFE[3]
    weekly = card.build_periodic_card(_delta("weekly"), tmp_path, font_path=korean_font)
    monthly = card.build_periodic_card(_delta("monthly"), tmp_path, font_path=korean_font)
    assert re.fullmatch(r"weekly-2026-10-11-[0-9a-f]{16}\.png", Path(weekly.path).name)
    assert re.fullmatch(r"monthly-2026-10-11-[0-9a-f]{16}\.png", Path(monthly.path).name)
    assert weekly.sha256 != monthly.sha256
    assert "글자 없음" in weekly.alt and "주간 결산" in weekly.alt and "월간 결산" in monthly.alt
    assert "2026-10-04" in weekly.alt and "2026-10-11" in weekly.alt and "출처 국토교통부 RTMS" in weekly.alt


def test_periodic_card_follows_gu_change(korean_font):
    a = card.draw_periodic_card(card.prepare_periodic_card(_delta()), korean_font).png_bytes()
    b = card.draw_periodic_card(card.prepare_periodic_card(_delta(deltas={"강남": -1.5})), korean_font).png_bytes()
    assert a != b
    data = card.prepare_periodic_card(_delta(deltas={"강남": 1.5, "노원": None}))
    assert data.signal == "price" and dict(data.gu_values)["강남"] == 1.5 and dict(data.gu_values)["노원"] is None


def test_weekly_card_uses_new_filings_by_gu_when_available(korean_font):
    """10-11 실측: 주간 구 중위 변화가 25개 구 전부 0.0 — 가격 신호만 쓰면 매주 같은 그림. 새 기록 수가 기본 신호."""
    flat = _delta(deltas={g: 0.0 for g in ("강남", "노원", "마포")})
    a = dict(flat, filings={"by_gu": {"강남": 40, "노원": 10}})
    b = dict(flat, filings={"by_gu": {"강남": 5, "노원": 60}})
    data = card.prepare_periodic_card(a)
    assert data.signal == "filings" and dict(data.gu_values) == {"강남": 40, "노원": 10, "마포": 0.0}
    pa = card.draw_periodic_card(data, korean_font).png_bytes()
    pb = card.draw_periodic_card(card.prepare_periodic_card(b), korean_font).png_bytes()
    pf = card.draw_periodic_card(card.prepare_periodic_card(flat), korean_font).png_bytes()
    assert len({pa, pb, pf}) == 3
    assert "새로 나타난 실거래 기록" in card._periodic_alt(data)


def test_media_contract_accepts_periodic_cards(korean_font, tmp_path):
    draft = write_digest_draft({"title": "주간결산", "tags": "주간결산", "tistory_html": "<p>" + "가" * 400 + "</p>"},
                               "2026-10-11", str(tmp_path), kind="periodic")
    built = card.build_periodic_card(_delta(), tmp_path / "images", font_path=korean_font)
    tm.write_media_manifest(draft, _parse_helper(draft), built)
    media = tm.read_media_manifest(draft, _parse_helper(draft), required=True)
    assert media.sha256 == built.sha256 and Path(media.image_path).name.startswith("weekly-")


def test_media_contract_still_rejects_unknown_card_names(korean_font, tmp_path):
    draft = write_digest_draft({"title": "주간결산", "tags": "주간결산", "tistory_html": "<p>" + "가" * 400 + "</p>"},
                               "2026-10-11", str(tmp_path), kind="periodic")
    built = card.build_periodic_card(_delta(), tmp_path / "images", font_path=korean_font)
    renamed = Path(built.path).with_name(Path(built.path).name.replace("weekly-", "yearly-"))
    Path(built.path).rename(renamed)
    with pytest.raises(tm.MediaContractError):
        tm.write_media_manifest(draft, _parse_helper(draft), card.ThumbnailCard(
            str(renamed), built.sha256, built.width, built.height, built.alt, built.byte_count,
            built.observed_date, built.data_asof, built.font_path))


def test_write_period_post_attaches_the_periodic_card(korean_font, tmp_path, monkeypatch):
    import json
    from blog import period_delta as pd
    from test_period_delta import _ds, _row
    monkeypatch.setenv("RE_PERIODIC_POSTS", "weekly")
    snap = tmp_path / "snapshots"; snap.mkdir()
    base = _ds([_row("강남", "A", 9.9), _row("노원", "C", 5.0)], asof="2026-08-30", gen="2026-09-06")
    (snap / "dataset-2026-09-06.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    res = pd.write_period_post(_ds([_row("강남", "A", 10.2), _row("노원", "C", 5.1)]), "2026-09-13", str(tmp_path))
    draft = res["draft"]
    media = tm.read_media_manifest(draft, _parse_helper(draft), required=True)
    assert Path(media.image_path).name.startswith("weekly-2026-09-13-") and res["image"] == media.image_path


def test_media_contract_binds_card_kind_to_draft_kind(korean_font, tmp_path):
    """일간 원고에 결산 그림, 결산 원고에 일간 그림을 붙이지 못한다."""
    payload = {"title": "t", "tags": "t", "tistory_html": "<p>" + "가" * 400 + "</p>"}
    daily_draft = write_digest_draft(payload, "2026-10-11", str(tmp_path))
    weekly = card.build_periodic_card(_delta(), tmp_path / "images", font_path=korean_font)
    with pytest.raises(tm.MediaContractError):
        tm.write_media_manifest(daily_draft, _parse_helper(daily_draft), weekly)


def test_price_signal_is_the_gu_median_change_the_alt_describes():
    """R6(Codex U12-1) — alt 는 '구 중위 변화'라 말한다. 단지별 변화율의 중위(delta_median_pct)가 아니라 gu_median_delta_pct.
    가격 [5,6,10,15,20]→[5.05,6.06,9,15.15,20.2]억: 구 중위 −10%, 단지별 변화율 중위 +1%."""
    d = {"kind": "weekly", "today": "2026-10-11", "asof": "2026-10-11", "base_date": "2026-10-04",
         "gus": [{"gu": "강남", "gu_median_delta_pct": -10.0, "delta_median_pct": 1.0, "n_valid": 5}]}
    assert dict(card.prepare_periodic_card(d).gu_values)["강남"] == -10.0


def test_filings_outside_seoul_or_invalid_counts_do_not_shape_the_card(korean_font):
    """R6(Codex U12-3·4, agy BUG-02·03) — 25구 밖 키가 최대값을 오염시키거나 음수 건수가 푸른 칸으로 칠해지지 않는다."""
    flat = _delta(deltas={"강남": 0.0, "노원": 0.0})
    clean = card.prepare_periodic_card(dict(flat, filings={"by_gu": {"강남": 10}}))
    dirty = card.prepare_periodic_card(dict(flat, filings={"by_gu": {"강남": 10, "부산": 1_000_000, "전국": 5000, "노원": -3}}))
    assert "부산" not in dict(dirty.gu_values) and "전국" not in dict(dirty.gu_values)
    assert dict(dirty.gu_values)["노원"] is None
    assert card.draw_periodic_card(clean, korean_font).png_bytes() == card.draw_periodic_card(dirty, korean_font).png_bytes()


def test_media_contract_binds_weekly_and_monthly_cards_to_the_draft_label(korean_font, tmp_path):
    """R6(agy BUG-01·Codex) — 주간·월간 원고는 파일명이 같아 태그(주간결산/월간결산)로 그림 종류를 묶는다. 쓰기 단계에서
    거부하면 원고·sidecar 를 건드리지 않는다."""
    payload = {"title": "t", "tags": "서울아파트,주간결산", "tistory_html": "<p>" + "가" * 400 + "</p>"}
    draft = write_digest_draft(payload, "2026-10-11", str(tmp_path), kind="periodic")
    before = Path(draft).read_text(encoding="utf-8")
    monthly = card.build_periodic_card(_delta("monthly"), tmp_path / "images", font_path=korean_font)
    with pytest.raises(tm.MediaContractError):
        tm.write_media_manifest(draft, _parse_helper(draft), monthly)
    assert Path(draft).read_text(encoding="utf-8") == before and not tm.media_path(draft).exists()
    weekly = card.build_periodic_card(_delta("weekly"), tmp_path / "images", font_path=korean_font)
    tm.write_media_manifest(draft, _parse_helper(draft), weekly)
    assert tm.read_media_manifest(draft, _parse_helper(draft), required=True).sha256 == weekly.sha256


def test_card_failure_on_rerun_discards_the_previous_card_contract(korean_font, tmp_path, monkeypatch):
    """R6(Codex U12-2) — 같은 날 재생성에서 카드가 실패하면 이전 실행의 표지·sidecar 가 새 원고와 어긋나 발행이
    ERR:representative image contract invalid 로 막혔다 → 둘 다 지우고 그림 없이 발행한다."""
    import json
    from blog import period_delta as pd
    from test_period_delta import _ds, _row
    monkeypatch.setenv("RE_PERIODIC_POSTS", "weekly")
    snap = tmp_path / "snapshots"; snap.mkdir()
    base = _ds([_row("강남", "A", 9.9), _row("노원", "C", 5.0)], asof="2026-08-30", gen="2026-09-06")
    (snap / "dataset-2026-09-06.json").write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    first = pd.write_period_post(_ds([_row("강남", "A", 10.2), _row("노원", "C", 5.1)]), "2026-09-13", str(tmp_path))
    assert tm.media_path(first["draft"]).exists()

    def boom(*args, **kwargs):
        raise OSError("font missing")
    monkeypatch.setattr(card, "build_periodic_card", boom)
    second = pd.write_period_post(_ds([_row("강남", "A", 10.6), _row("노원", "C", 5.3)]), "2026-09-13", str(tmp_path))
    assert second["draft"] == first["draft"] and "media" not in second
    assert not tm.media_path(second["draft"]).exists()
    assert "tistory-representative-sha256" not in Path(second["draft"]).read_text(encoding="utf-8")
    assert tm.read_media_manifest(second["draft"], _parse_helper(second["draft"])) is None


def test_discard_media_manifest_clears_a_half_written_contract(korean_font, tmp_path):
    """표지(meta)만 남고 sidecar 쓰기가 실패한 상태도 지운다."""
    payload = {"title": "t", "tags": "서울아파트,주간결산", "tistory_html": "<p>" + "가" * 400 + "</p>"}
    draft = write_digest_draft(payload, "2026-10-11", str(tmp_path), kind="periodic")
    built = card.build_periodic_card(_delta(), tmp_path / "images", font_path=korean_font)
    tm.write_media_manifest(draft, _parse_helper(draft), built)
    tm.media_path(draft).unlink()
    with pytest.raises(tm.MediaContractError):
        tm.read_media_manifest(draft, _parse_helper(draft))
    tm.discard_media_manifest(draft)
    assert tm.read_media_manifest(draft, _parse_helper(draft)) is None
