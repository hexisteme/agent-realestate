"""Risk Flag — 점수만으론 안 잡히는 하드 제약 (RDU-058 v2 Risk Flag).

핵심: 관리처분~이주 단계 단지는 토지지분 점수는 높아도 '1년 실거주 후 임대/보유'와
물리적으로 충돌(입주권 성격, 곧 이주). 점수에 곱셈 페널티를 적용해 *거주 필요 전략*의
1차 순위에서 강등하되, 리포트에서 '토지지분 올인 분기'로 보존한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..domain import Candidate, ExitStrategy

# 1년 실거주(이후 임대/매도/거주)를 전제하는 전략들
RESIDENCE_NEEDING = {ExitStrategy.HOLD_AND_RENT, ExitStrategy.LIVE_THEN_SELL, ExitStrategy.PRIMARY_ONLY}


@dataclass(frozen=True)
class RiskFlag:
    code: str
    message: str
    penalty: float   # 점수 곱셈 계수 (1.0=영향없음). ★데이터품질 플래그(F_STALE/F_NORENT)는 1.0 —
                     # 불확실성은 trust.py(결정신뢰도)가 전담, 가치점수를 깎으면 범주혼동+이중계상
                     # (3차 감사 C, council 4인 만장일치 2026-06-11). 하드제약(F_MOVEOUT/F_OVERBUDGET)만 <1.0.


# 하드페일 코드 단일 소스 (3차 감사 B: assembler/_hard_fail·compset·cli·email 4곳이 제각각 판정하던 것 통일)
HARD_FAIL_CODES = ("F_OVERBUDGET", "F_MOVEOUT")


def has_hard_fail(flags) -> bool:
    """예산초과·이주단계 등 '매수 불가' 하드제약 보유 여부 — 랭킹 partition 의 단일 기준.
    flags 는 RiskFlag 또는 코드 문자열 혼용 허용(구 compset 호환)."""
    return any(getattr(f, "code", f) in HARD_FAIL_CODES for f in flags)


def ranking_key(flags, adjusted_fundamental: float) -> tuple:
    """랭킹 정렬키 단일 소스: (통과여부, 호가무관 조정점수) — reverse=True 정렬용.
    cli score_top·assembler §A·email 우선순위가 전부 이 키를 써야 1위가 일치한다(3차 감사 B)."""
    return (not has_hard_fail(flags), adjusted_fundamental)


def assess_flags(c: Candidate, strategy: ExitStrategy, today: date | None = None,
                 stale_days: int = 14) -> list[RiskFlag]:
    flags: list[RiskFlag] = []
    if today is not None and c.listing.is_stale(today, stale_days):
        d = (today - c.listing.confirmed_date).days
        flags.append(RiskFlag("F_STALE",
            f"호가 확인일 {d}일 경과({c.listing.confirmed_date:%y.%m.%d}) — 재확인 필요(staleness 게이트).", 1.0))
    if strategy in RESIDENCE_NEEDING and c.redev_stage.blocks_residence:
        flags.append(RiskFlag(
            "F_MOVEOUT",
            f"{c.redev_stage.label} 단계 → 1년 실거주·임대 충돌(입주권 성격, 이주 임박). "
            f"'토지지분 올인'(거주 양보) 분기에서만 후보.",
            0.6,
        ))
    if (strategy is ExitStrategy.HOLD_AND_RENT and c.jeonse_krw is None
            and not c.redev_stage.blocks_residence):
        flags.append(RiskFlag(
            "F_NORENT", "전세 호가 미확보 — 임대 수요/전환 가능성 직접 확인 필요"
            "(신뢰도 '전세호가=없음' 반영, 점수 비차감).", 1.0,
        ))
    return flags


def penalty_product(flags: list[RiskFlag]) -> float:
    p = 1.0
    for f in flags:
        p *= f.penalty
    return p
