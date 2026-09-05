"""금칙어 가드(2026-09-05 P1) — A모델(실명 사실 레이어) 발행물에 평가·추천성 어휘가 섞이면
발행을 조용히 통과시키지 않고 크게 실패한다. 다이제스트·구허브·주간요약 생성기 내부에서
완성 텍스트에 대해 호출한다(호출 자체는 각 생성기의 의무 — 여기선 판정만).
"""
from __future__ import annotations

FORBIDDEN_WORDS = (
    "저평가", "고평가", "유망", "추천", "1위", "급등", "급락", "폭등", "폭락",
    "매수 권유", "호가", "KB시세",
)


def assert_wording_ok(text: str, where: str) -> None:
    """text 에 FORBIDDEN_WORDS 중 하나라도 있으면 ValueError(where·적발어 포함)로 크게 실패한다."""
    hits = [w for w in FORBIDDEN_WORDS if w in text]
    if hits:
        raise ValueError(f"[wording-guard] 금칙어 발견 in {where}: {hits}")
