"""금칙어 가드(2026-09-05 P1, 2026-09-06 리드 패턴 추가) — A모델(실명 사실 레이어) 발행물에
평가·추천성 어휘가 섞이면 발행을 조용히 통과시키지 않고 크게 실패한다. 다이제스트·구허브·주간요약
생성기 내부에서 완성 텍스트에 대해 호출한다(호출 자체는 각 생성기의 의무 — 여기선 판정만).

LEAD_FORBIDDEN_PATTERNS/assert_lead_wording_ok 는 fact_lead.py 가 만드는 리드 문장 전용 추가 규칙 —
서수·상하위 N곳·최상급·전망·인과단정을 막는다(§7 A1 "사실로 계산한 서열도 서열이다"). 기존 다이제스트
표 제목("전세가율 상위 5")은 값 정렬일 뿐 서수가 아니라 이 정규식 세트의 적용 대상이 아니다 — 리드
문장에만 건다.
"""
from __future__ import annotations
import re

FORBIDDEN_WORDS = (
    "저평가", "고평가", "유망", "추천", "1위", "급등", "급락", "폭등", "폭락",
    "매수 권유", "호가", "KB시세",
)

LEAD_FORBIDDEN_PATTERNS = (
    r"\d+\s*위\b", r"번째로", r"상위\s*\d+\s*곳", r"하위\s*\d+\s*곳",
    r"가장\s*(높|낮|비싼|싼)", r"최고의|최저의|최악|최고가\s*단지",
    r"전망|예상|것이다|될\s*것", r"때문에|덕분에|영향으로",
)


def assert_wording_ok(text: str, where: str) -> None:
    """text 에 FORBIDDEN_WORDS 중 하나라도 있으면 ValueError(where·적발어 포함)로 크게 실패한다."""
    hits = [w for w in FORBIDDEN_WORDS if w in text]
    if hits:
        raise ValueError(f"[wording-guard] 금칙어 발견 in {where}: {hits}")


def assert_lead_wording_ok(text: str, where: str) -> None:
    """FactLead 문장 전용 — 금칙어(위) + 서수/최상급/전망/인과 정규식까지 통과해야 한다."""
    assert_wording_ok(text, where)
    hits = [p for p in LEAD_FORBIDDEN_PATTERNS if re.search(p, text)]
    if hits:
        raise ValueError(f"[wording-guard] 리드 금칙 패턴 발견 in {where}: {hits} — text={text!r}")
