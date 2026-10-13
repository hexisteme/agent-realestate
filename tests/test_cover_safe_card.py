"""커버 안전 대표 카드(2026-10-10) — 스킨이 1200×630 을 커버 크롭하고 제목을 하단에 겹쳐도 핵심 수치가 남는다.

실측(live115 모바일 415px): 카드는 752×395 로 커버 확대돼 가로 중앙 55%만 보이고(360px 폰은 48%),
제목 오버레이가 세로 50~92%를 덮는다 → 안전영역 x 312~888, y ≤ 300."""
import pytest

from blog import thumbnail_card as card
from test_thumbnail_card import _dataset

SAFE = (312, 0, 888, 300)


@pytest.fixture
def korean_font():
    pytest.importorskip("PIL")
    try:
        return card._resolve_font()
    except FileNotFoundError:
        pytest.skip("Korean font unavailable on this host")


def test_cover_card_has_no_text_and_keeps_the_tile_pattern_in_the_cropped_center(korean_font):
    """외부 리뷰(2026-10-13) U1·U12 — 데스크톱 스킨이 대표이미지를 제목 배경으로 깔아 카드 글자와 제목이 겹쳤다.
    대표이미지는 글자 없는 서울 25개 구 타일 그림으로 바꾼다(칸 밝기 = 구별 상승 단지 비율, 날마다 달라짐)."""
    data = card.prepare_daily_card(_dataset_with_gu())
    rendered = card.draw_daily_card(data, korean_font, cover_safe=True)
    assert rendered.text_boxes == []
    assert len(rendered.tile_boxes) == 25
    for left, top, right, bottom in rendered.tile_boxes:
        assert SAFE[0] <= left and right <= SAFE[2] and bottom <= SAFE[3], (left, top, right, bottom)
    assert rendered.png_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def _dataset_with_gu():
    ds = _dataset()
    for i, row in enumerate(ds["complexes"]):
        row["gu"] = ["강남", "노원", "강남", "마포", "송파"][i]
    return ds


def test_cover_card_brightness_follows_gu_up_share(korean_font):
    ds = _dataset_with_gu()
    shares = dict(card.prepare_daily_card(ds).gu_up_shares)
    # 강남: 판정 1(▲, 두 번째 강남 행은 표본 5건이라 게이트 미통과) · 노원: 판정 1(▼) · 종로: 행 없음
    assert shares["강남"] == 1.0 and shares["노원"] == 0.0 and shares["종로"] is None
    flipped = _dataset_with_gu()
    flipped["complexes"][1]["molit_trend_dir"], flipped["complexes"][1]["molit_trend_pct"] = "▲", 3.0
    a = card.draw_daily_card(card.prepare_daily_card(ds), korean_font, cover_safe=True).png_bytes()
    b = card.draw_daily_card(card.prepare_daily_card(flipped), korean_font, cover_safe=True).png_bytes()
    assert a != b


def test_default_card_layout_is_unchanged(korean_font, tmp_path):
    data = card.prepare_daily_card(_dataset())
    assert card.draw_daily_card(data, korean_font).png_bytes() == \
           card.draw_daily_card(data, korean_font, cover_safe=False).png_bytes()
    plain = card.build_daily_card(_dataset(), tmp_path, font_path=korean_font)
    safe = card.build_daily_card(_dataset(), tmp_path, font_path=korean_font, cover_safe=True)
    assert plain.sha256 != safe.sha256
    assert "글자 없음" in safe.alt and "관측일" in safe.alt   # 글자 없는 그림을 숫자가 있는 것처럼 설명하지 않는다
