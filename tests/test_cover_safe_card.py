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


def test_cover_safe_card_keeps_brand_count_and_direction_in_the_cropped_center(korean_font):
    data = card.prepare_daily_card(_dataset())
    rendered = card.draw_daily_card(data, korean_font, cover_safe=True)
    key = rendered.text_boxes[:4]
    assert len(key) == 4
    for left, top, right, bottom in key:
        assert SAFE[0] <= left and right <= SAFE[2] and bottom <= SAFE[3], (left, top, right, bottom)
        assert abs((left + right) / 2 - 600) <= 2   # 가운데 정렬
    assert rendered.png_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_default_card_layout_is_unchanged(korean_font, tmp_path):
    data = card.prepare_daily_card(_dataset())
    assert card.draw_daily_card(data, korean_font).png_bytes() == \
           card.draw_daily_card(data, korean_font, cover_safe=False).png_bytes()
    plain = card.build_daily_card(_dataset(), tmp_path, font_path=korean_font)
    safe = card.build_daily_card(_dataset(), tmp_path, font_path=korean_font, cover_safe=True)
    assert plain.sha256 != safe.sha256 and plain.alt == safe.alt
