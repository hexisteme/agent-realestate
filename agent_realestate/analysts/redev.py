"""재건축/재개발 사업성 + 토지지분 평가 — 결정론 (RDU-058 가격방어/상승 축 입력).

토지지분은 '평수'가 아니라 '평당 토지가(매매가÷대지지분)'로도 본다 (sage/council 합의).
LLM 확률 금지 — 단계·용적률·대지지분·비례율의 결정론 점수만 산출.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import Candidate, RedevStage

EOK = 100_000_000


@dataclass(frozen=True)
class RedevScore:
    stage_level: int
    stage_label: str
    far_pct: float
    land_share_pyeong: float
    land_value_per_pyeong_krw: int   # 매매가 ÷ 대지지분
    feasibility_0to5: float          # 결정론 사업성 점수
    note: str


def _far_score(far_pct: float) -> float:
    """용적률 낮을수록 재건축 사업성↑ (저층 주공 등)."""
    if far_pct <= 0:
        return 3.0
    if far_pct < 130:
        return 5.0
    if far_pct < 180:
        return 4.0
    if far_pct < 220:
        return 2.5
    return 1.5  # 220%+ 재건축 난망


def _stage_score(stage: RedevStage) -> float:
    # 단계가 진행될수록 불확실성↓ → 사업성 점수↑ (단 이주 단계는 거주 불가 이슈는 별도)
    return min(5.0, 1.0 + stage.level * 0.7)


def score_redev(c: Candidate) -> RedevScore:
    land = max(c.land_share_pyeong, 0.1)
    lvpp = int(c.listing.price_krw / land)
    # 사업성 = 용적률(0.5) + 단계(0.5) 가중. 단계 NONE 이면 사업성 상한 강제 하향.
    feas = _far_score(c.far_pct) * 0.5 + _stage_score(c.redev_stage) * 0.5
    if c.redev_stage is RedevStage.NONE:
        feas = min(feas, 2.0)  # 공식 단계 없으면 재건축 가치 기대 낮음
    note_bits = [f"단계={c.redev_stage.label}", f"용적률={c.far_pct:.0f}%",
                 f"대지지분≈{land:.1f}평{'(추정)' if c.land_share_is_estimate else ''}",
                 f"토지평당≈{lvpp/EOK*10000:.0f}만원/평"]
    return RedevScore(
        stage_level=c.redev_stage.level,
        stage_label=c.redev_stage.label,
        far_pct=c.far_pct,
        land_share_pyeong=land,
        land_value_per_pyeong_krw=lvpp,
        feasibility_0to5=round(feas, 2),
        note=" · ".join(note_bits),
    )
