"""금칙어 가드 단위테스트(2026-09-05 P1). 대상: blog.wording_guard.FORBIDDEN_WORDS / assert_wording_ok."""
from __future__ import annotations
import pytest

from blog.wording_guard import FORBIDDEN_WORDS, assert_wording_ok


def test_forbidden_words_contains_known_terms():
    for w in ("저평가", "고평가", "유망", "추천", "1위", "급등", "급락", "폭등", "폭락",
              "매수 권유", "호가", "KB시세"):
        assert w in FORBIDDEN_WORDS


def test_clean_text_passes_without_raising():
    assert assert_wording_ok("국토부 실거래 중위 8.5억, 표본 12건.", "unit-test") is None


def test_single_forbidden_word_raises_value_error():
    with pytest.raises(ValueError, match="추천"):
        assert_wording_ok("이 단지는 추천 매물입니다.", "unit-test:single")


def test_where_label_included_in_error_message():
    with pytest.raises(ValueError, match=r"unit-test:where-label"):
        assert_wording_ok("1위 아파트", "unit-test:where-label")


def test_multiple_forbidden_words_all_listed_in_message():
    with pytest.raises(ValueError) as exc:
        assert_wording_ok("이 저평가 단지는 급등 예상 1위 후보", "unit-test:multi")
    msg = str(exc.value)
    for w in ("저평가", "급등", "1위"):
        assert w in msg


def test_forbidden_substring_trips_even_inside_negation_sentence():
    # 2026-09-05 실사고 재현: daily_digest/gu_hub 각주가 "사설 호가·민간시세 미포함"이라고 썼다가
    # '~미포함'(부정)이어도 '호가' 부분문자열이 그대로 걸려 발행이 막혔던 실제 결함.
    with pytest.raises(ValueError, match="호가"):
        assert_wording_ok("사설 호가·민간시세 미포함", "unit-test:substring")


def test_forbidden_word_absent_does_not_raise_for_similar_but_different_word():
    # "매수"만으로는 걸리지 않아야 함(금칙어는 "매수 권유" 구절 단위)
    assert assert_wording_ok("이 단지는 매수 문의가 있었다.", "unit-test:not-a-hit") is None
