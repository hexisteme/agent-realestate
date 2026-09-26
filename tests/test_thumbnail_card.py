"""대표 카드의 관측 범위·결측값·이미지 무결성·원자출력 계약."""
import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest

from blog import thumbnail_card as card


def _row(n=12, price=8.0, area=59.0, kind="아파트", trend=None):
    return {"molit_n": n, "molit_recent_eok": price, "area_m2": area,
            "product_type": kind, "molit_trend_dir": trend}


def _dataset():
    return {"count": 9000, "complexes": [
        _row(trend="▲"), _row(price=12.0, trend="▼"), _row(n=5, price=99.0),
        _row(area=35.0, price=77.0), _row(kind="주상복합", price=88.0),
    ], "generated": "2026-09-26", "data_asof": "2026-09-25"}


@pytest.fixture
def korean_font():
    pytest.importorskip("PIL")
    try:
        return card._resolve_font()
    except FileNotFoundError:
        pytest.skip("Korean font unavailable on this host")


def test_counts_use_actual_rows_and_median_obeys_existing_gate():
    data = card.prepare_daily_card(_dataset())
    assert data.complex_count == 5
    assert data.sample_count == 53
    assert (data.up_count, data.down_count) == (1, 1)
    assert data.median_eok == 10.0
    assert data.median_complex_count == 2
    assert data.band_counts == (1, 1, 0, 0)
    assert "9,000" not in data.alt
    assert "5단지" in data.alt and "53건" in data.alt
    assert "수집 대상은 아파트·다세대·연립 등 주거 유형" in data.alt
    assert "단지 대표면적 실거래 표본 53건" in data.alt


def test_integer_sample_count_does_not_round_through_float():
    ds = _dataset()
    ds["complexes"][0]["molit_n"] = 2**53 + 1
    assert card.prepare_daily_card(ds).sample_count == 2**53 + 42


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 1.5, "10", True, 10**400])
def test_contaminated_sample_is_unknown_and_not_a_partial_sum(bad):
    ds = _dataset()
    ds["complexes"][0]["molit_n"] = bad
    data = card.prepare_daily_card(ds)
    assert data.sample_count is None
    assert "sample_count_unavailable" in data.issues
    assert "단지 대표면적 실거래 표본 미확인건" in data.alt
    assert data.median_complex_count == 1


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -10, "8억", True])
def test_corrupted_prices_do_not_enter_price_aggregate(bad):
    ds = _dataset()
    ds["complexes"][0]["molit_recent_eok"] = bad
    data = card.prepare_daily_card(ds)
    assert data.median_eok == 12.0
    assert data.band_counts == (0, 1, 0, 0)


@pytest.mark.parametrize("bad", [None, "rows", [None]])
def test_unavailable_dataset_is_not_reported_as_zero_observation(bad):
    data = card.prepare_daily_card({"complexes": bad, "generated": "<script>", "data_asof": None})
    assert data.complex_count is None
    assert data.sample_count is None
    assert data.observed_date == "미확인"
    assert data.data_asof == "미확인"
    assert data.up_count is None
    assert data.median_eok is None


def test_output_is_deterministic_named_by_hash_and_real_png(tmp_path, korean_font):
    from PIL import Image
    first = card.build_daily_card(_dataset(), tmp_path, font_path=korean_font)
    path = Path(first.path)
    mtime = path.stat().st_mtime_ns
    second = card.build_daily_card(_dataset(), tmp_path, font_path=korean_font)
    assert first == second
    assert path.stat().st_mtime_ns == mtime
    assert path.is_absolute()
    assert first.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert first.sha256[:16] in path.name
    assert first.byte_count < card.CARD_MAX_BYTES
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.size == (1200, 630)
        image.verify()
    assert not list(tmp_path.glob(".daily-card-*"))


def test_new_observation_changes_hash(tmp_path, korean_font):
    first = card.build_daily_card(_dataset(), tmp_path, font_path=korean_font)
    ds = _dataset()
    ds["complexes"].append(_row(n=25))
    second = card.build_daily_card(ds, tmp_path, font_path=korean_font)
    assert first.sha256 != second.sha256
    assert "6단지" in second.alt and "78건" in second.alt


def test_extreme_display_values_and_long_text_remain_inside_canvas(korean_font):
    ds = _dataset()
    ds["complexes"][0]["molit_n"] = 10**80
    rendered = card.draw_daily_card(card.prepare_daily_card(ds), korean_font)
    rendered.text("아주 긴 관측 설명 " * 100, (50, 100, 500, 150), size=30)
    assert rendered.text_boxes
    assert all(0 <= l <= r <= card.CARD_WIDTH and 0 <= t <= b <= card.CARD_HEIGHT
               for l, t, r, b in rendered.text_boxes)
    assert rendered.png_bytes()


def test_write_failure_does_not_expose_partial_png(tmp_path, korean_font):
    with patch.object(card.os, "replace", side_effect=OSError("simulated commit failure")):
        with pytest.raises(OSError, match="simulated"):
            card.build_daily_card(_dataset(), tmp_path, font_path=korean_font)
    assert list(tmp_path.iterdir()) == []


def test_missing_font_has_explicit_failure(tmp_path):
    with pytest.raises(FileNotFoundError, match="font_path"):
        card.build_daily_card(_dataset(), tmp_path, font_path=tmp_path / "missing.ttf")
    assert list(tmp_path.iterdir()) == []


def test_social_meta_is_absolute_and_html_escaped():
    result = card.social_meta("https://example.test/blog", "assets/og/daily.png", '<card> "서울"')
    assert 'content="https://example.test/blog/assets/og/daily.png"' in result
    assert '&lt;card&gt; &quot;서울&quot;' in result
    assert 'content="1200"' in result and 'content="630"' in result
    assert 'content="summary_large_image"' in result


@pytest.mark.parametrize("base,path", [
    ("http://example.test/blog", "a.png"), ("//example.test/blog", "a.png"),
    ("https://user:secret@example.test/blog", "a.png"),
    ("https://example.test/blog?x=1", "a.png"),
    ("https://example.test/blog", "https://elsewhere.test/a.png"),
    ("https://example.test/blog", "../a.png"), ("https://example.test/blog", "/a.png"),
    ("https://example.test/blog", "%2e%2e/a.png"),
    ("https://example.test/blog", "assets//a.png"),
    ("https://example.test/blog", "a.png#payload"),
    ("https://example.test/blog", "a.svg"),
])
def test_social_meta_rejects_unowned_image_paths(base, path):
    with pytest.raises(ValueError):
        card.social_meta(base, path, "서울")
