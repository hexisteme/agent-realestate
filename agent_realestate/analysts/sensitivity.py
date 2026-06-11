"""민감도 분석 (P1-4, turing). 1순위가 입력 교란에 robust 한가 = 반증 임계.

★3차 감사 A(2026-06-11) 재작성: 기존은 폐기 지표 weighted_total(assembler 별지 D 강등)을
교란해 robust/flipped 를 판정 — 실순위(adjusted_fundamental, 호가무관)와 지표 불일치로
별지 A 의 반증임계가 본 랭킹에 대해 무효였다(2026-06-06 단일화의 미적용 잔존).
교체 후: ① 순위 지표 = fundamental × penalty + 하드페일 partition (assembler §A 와 동일 키)
② 매매가/전세 ±N% 교란 제거 — 호가무관 설계상 fundamental 에 항상-무변동이라
   '가짜 robust' 신호만 만들던 것(부수결함). 가격 교란의 의미는 호가분리 설계가 이미 흡수.
③ 유효 경쟁자(하드페일 제외 통과 후보) ≤1 이면 검정 무의미를 명시 행으로 공시
   (affordable-2real 사례: 후보 2 중 1 하드페일 → 'robust'가 '경쟁자 부재'의 오기였음).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import Candidate, ExitStrategy
from .redev import score_redev
from .risk import has_hard_fail, penalty_product
from .scoring import AXES, WEIGHTS, score_candidates


@dataclass(frozen=True)
class SensitivityRow:
    label: str
    new_top: str
    new_top_adjusted: float
    flipped: bool


def _rank_top(candidates: list[Candidate], strategy: ExitStrategy,
              flags_by_idx: list[tuple], weights: dict | None = None) -> tuple[str, float]:
    """실순위와 동일 키: (하드페일 partition, fundamental × penalty) — assembler §A 정렬과 일치."""
    redevs = [score_redev(c) for c in candidates]
    axes = score_candidates(candidates, redevs, strategy, weights=weights)
    adjs = [round(a.fundamental_total * penalty_product(list(flags_by_idx[i])), 3)
            for i, a in enumerate(axes)]
    best_i = max(range(len(axes)),
                 key=lambda i: (not has_hard_fail(flags_by_idx[i]), adjs[i]))
    return candidates[best_i].listing.complex_name, adjs[best_i]


def _reweight(strategy: ExitStrategy, axis: str, factor: float) -> dict:
    """한 축 가중치를 factor 배 한 뒤 합=1 로 재정규화 (가중치 교란용)."""
    w = dict(WEIGHTS[strategy])
    w[axis] = w[axis] * factor
    s = sum(w.values())
    return {k: v / s for k, v in w.items()}


def sensitivity_analysis(candidates: list[Candidate], strategy: ExitStrategy,
                         flags_by_idx: list[tuple], base_top: str) -> list[SensitivityRow]:
    """순위를 실제로 지배하는 변수 = 가중치(설계자 판단)만 교란한다.
    매매가/전세 교란은 호가무관 순위에 구조상 inert — 표시하면 '가짜 robust'라 제거(3차 감사 A)."""
    rows: list[SensitivityRow] = []
    # 유효 경쟁자 공시: 통과(비하드페일) 후보 ≤1 이면 어떤 교란도 1위를 못 바꿈 — 검정 전제 붕괴.
    n_pass = sum(1 for fl in flags_by_idx if not has_hard_fail(fl))
    if n_pass <= 1:
        rows.append(SensitivityRow(
            label=f"⚠ 유효 경쟁자 없음(하드페일 제외 통과 {n_pass}곳) — 민감도 검정 무의미(robust 아님)",
            new_top=base_top, new_top_adjusted=0.0, flipped=False))
    # 가중치 교란: 비중 가장 큰 두 축을 ±30% (가중치는 설계자 판단 → 결론의 진짜 구속 변수)
    w = WEIGHTS[strategy]
    top_axes = sorted(AXES, key=lambda a: w[a], reverse=True)[:2]
    for ax in top_axes:
        for factor, lbl in ((0.70, "-30%"), (1.30, "+30%")):
            top, adj = _rank_top(candidates, strategy, flags_by_idx,
                                 _reweight(strategy, ax, factor))
            rows.append(SensitivityRow(label=f"가중치 {ax} {lbl}", new_top=top,
                                       new_top_adjusted=adj, flipped=(top != base_top)))
    return rows
