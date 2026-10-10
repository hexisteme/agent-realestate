"""Tistory 일일 원고의 제목 구조 — 스킨의 글 제목이 h2.title-article 이므로 본문 섹션은 h3, 그 아래는 h4.

design-review 2026-10-09 floker-114 B-3: 본문에 h1~h6 가 없고 굵은 문단이 섹션 라벨 역할을 했다. Tistory 는 문단의
인라인 style 을 지우므로(115 실측: style → data-ke-size) 굵은 문단은 발행 뒤 일반 문단으로 보인다.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from blog.daily_digest import build_daily_digest

DATASET = Path(__file__).parents[1] / "report/blog/dataset.json"
QUESTION = "서울 아파트 매수를 검토할 때, 어떤 실거래 관측부터 확인하면 좋을까요?"
SECTIONS = ("서울 집값과 주거지원, 한눈에", QUESTION, "거시 지표 — 금리와 주택시장", "청약·공공임대 안내",
            "서울 25개 구 요약 · 가격 히트맵")
SUBSECTIONS = ("12개월 회전율 상위 관측", "52주 범위 하단 근접 관측", "① 새 주택담보대출 금리")


def _headings(document: str) -> list[tuple[int, str]]:
    return [(int(level), " ".join(re.sub(r"<[^>]+>", "", text).split()))
            for level, text in re.findall(r"<h([1-6])\b[^>]*>(.*?)</h\1>", document, re.S)]


@pytest.fixture(scope="module")
def digest():
    if not DATASET.exists():
        pytest.skip("report/blog/dataset.json 없음")
    ds = json.loads(DATASET.read_text(encoding="utf-8"))
    return build_daily_digest(ds, "2026-10-10", "2026-10-10")


def test_tistory_body_uses_h3_sections_and_h4_subsections(digest):
    document = digest["tistory_html"]
    headings = _headings(document)

    assert headings, "본문에 제목이 하나도 없다"
    assert {level for level, _ in headings} <= {3, 4}
    assert headings[0][0] == 3
    for label in SECTIONS:
        assert any(level == 3 and text.startswith(label) for level, text in headings), label
        assert f"<b>{label}</b>" not in document
    for label in SUBSECTIONS:
        assert any(level == 4 and text.startswith(label) for level, text in headings), label
        assert f"<b>{label}</b>" not in document


def test_site_keeps_h1_then_h2_for_the_same_sections(digest):
    headings = _headings(digest["site_html"])

    assert [level for level, _ in headings].count(1) == 1
    assert (2, QUESTION) in headings
    assert any(level == 3 and text.startswith("12개월 회전율 상위 관측") for level, text in headings)
    assert any(level == 2 and text.startswith("청약·공공임대 안내") for level, text in headings)
