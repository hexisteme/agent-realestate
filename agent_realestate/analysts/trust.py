"""결정 신뢰도 지표 (2026-05-29) — 리포트가 *자신의 신뢰수준을 수치로 보고*한다.

배경: 3개 소스(Claude·sage·council)가 공통으로 지목한 #1 상한은 '입력 진위검증 부재'.
호가·재건축단계·대지지분이 수동 주입 = 코어가 진위를 검증하지 못함. 종전 대응은 정성 배너
("독립확인 필수")뿐이었다. 그러면 "얼마나 신뢰할 수 있나"에 답이 안 된다.

대응: 각 후보의 의사결정-구속 입력이 **검증/교차검증/수동/추정/약함** 중 무엇인지 결정론으로
분류하고, 의사결정 영향 가중치로 합산해 **결정신뢰도(%)** 와 등급을 낸다. 전부 결정론(G3) —
LLM 이 신뢰도를 지어내지 않는다. 이 지표 자체가 객관 지표이며, '95% 신뢰'를 *주장*하는 대신
*측정*한다 (council wittgenstein: 재현성≠적중률 — 여기서 측정하는 건 '입력 검증 완성도').
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import Candidate, DataSource, ExitStrategy

# 입력 검증 수준 → 신뢰 기여. 검증=직접 출처인증(MOLIT/등기부/정책/카카오/정비사업)=1.0.
# 교차검증=근접 대조로 검증(호가↔실거래)=0.9(직접 인증은 아님). 수동=0.5, 추정=0.2, 없음=0.
_LEVEL_VALUE = {"검증": 1.0, "교차검증": 0.9, "수동": 0.5, "약함": 0.3, "추정": 0.2, "없음": 0.0}

# 의사결정 영향 가중치 (합 1.0). 호가가 최대 — 모든 자본·세금·시나리오의 출발점.
_WEIGHTS = {
    "호가": 0.30, "실거래추세": 0.20, "대지지분": 0.15,
    "재건축단계": 0.15, "세율정책": 0.10, "입지": 0.10,
}


@dataclass(frozen=True)
class TrustComponent:
    field: str
    level: str
    weight: float
    note: str


@dataclass(frozen=True)
class TrustScore:
    candidate_name: str
    components: tuple[TrustComponent, ...]
    score_pct: float          # 0~100, 가중 검증완성도
    grade: str                # "의사결정 가능"|"조건부"|"참고만"
    blocking: tuple[str, ...]  # 점수를 올리려면 독립확인해야 할 것


def _grade(pct: float) -> str:
    if pct >= 85.0:
        return "의사결정 가능"
    if pct >= 60.0:
        return "조건부(독립확인 후)"
    return "참고만"


def assess_trust(c: Candidate, *, trend=None, policy_is_default: bool = True,
                 has_location_signal: bool = False, redev_verified: bool = False,
                 strategy: "ExitStrategy | None" = None) -> TrustScore:
    """결정신뢰도 = *입력 진위(authenticity)* 측정. 주의: '예측 강도'(추세가 외삽에 쓸 만한가)는
    여기서 점수화하지 않는다 — 그건 §6 밴드/강도의 일이다. MOLIT 실거래는 강도와 무관하게
    '진짜 사실'이므로 진위 점수에선 검증으로 친다. (두 질문을 섞으면 진짜 데이터를 거짓 페널티함)
    ★전세호가(2026-06-07 적대감사 후속): HOLD_AND_RENT 는 전세=레버리지·현금흐름 구속입력이라
    진위에 포함 — 전세 미수집(전세수요 표본평균 임퓨트)이 신뢰도에 반영되도록(이전엔 §5 ⁱ플래그만)."""
    # 가중치: HOLD 면 전세호가 0.10 편입(기존 6축 ×0.9). 그 외 전략은 전세 비구속이라 기존 가중 유지.
    W = dict(_WEIGHTS)
    if strategy is ExitStrategy.HOLD_AND_RENT:
        W = {k: round(v * 0.9, 4) for k, v in _WEIGHTS.items()}
        W["전세호가"] = 0.10
    comps: list[TrustComponent] = []

    # 호가: 네이버 라이브여도 '수동 주입'(코어 미검증). MOLIT 실거래와 괴리 작으면 교차검증으로 승격.
    if c.listing.source is DataSource.NAVER_LIVE_CHROME and trend is not None:
        gap = abs(c.listing.price_krw / trend.last_price_krw - 1) * 100
        if gap <= 10.0:
            comps.append(TrustComponent("호가", "교차검증", W["호가"],
                                        f"네이버 라이브 ↔ MOLIT 실거래 괴리 {gap:.0f}%(≤10%, 교차검증)"))
        else:
            comps.append(TrustComponent("호가", "수동", W["호가"],
                                        f"네이버 라이브, 단 실거래 대비 괴리 {gap:.0f}%(>10%, 독립확인 필요)"))
    else:
        comps.append(TrustComponent("호가", "수동", W["호가"],
                                    "네이버 라이브 수동주입 — 실거래 교차검증 데이터 없음"))

    # 실거래추세: MOLIT 실거래는 *진짜 사실*이므로 진위=검증 (강도와 무관).
    # 예측 강도(약/중/강)는 §6 밴드에서 별도 표시 — 진위 점수에 섞지 않는다.
    if trend is None:
        comps.append(TrustComponent("실거래추세", "없음", W["실거래추세"], "실거래 시계열 미적재(fetch-molit)"))
    else:
        fc = "" if trend.strength != "약" else " (단, 예측강도 '약' — §6 밴드 참조)"
        comps.append(TrustComponent("실거래추세", "검증", W["실거래추세"],
                                    f"MOLIT {trend.n}건 실거래(진짜 사실){fc}"))

    # 대지지분: 등기부 실측이면 검증, 추정이면 추정 (이 사용자 1순위 가치축이라 가중 큼)
    comps.append(TrustComponent("대지지분",
                                "추정" if c.land_share_is_estimate else "검증", W["대지지분"],
                                "용적률·세대수 추정값" if c.land_share_is_estimate else "등기부 실측"))

    # 재건축단계: 출처있는 캐시(정비사업 API/공식자료)면 검증, 아니면 수동
    comps.append(TrustComponent("재건축단계", "검증" if redev_verified else "수동", W["재건축단계"],
                                "출처있는 정비사업 자료" if redev_verified
                                else "수동주입 — 매수 전 조합/구청 확인 필요"))

    # 세율정책: 정책캐시 주입이면 검증, 코드 기본값이면 추정(미검증)
    comps.append(TrustComponent("세율정책", "추정" if policy_is_default else "검증", W["세율정책"],
                                "코드 기본값(미검증)" if policy_is_default else "정책캐시 주입(출처·확인일)"))

    # 입지: 카카오 정량 신호 있으면 검증, 없으면 수동(free-text)
    comps.append(TrustComponent("입지", "검증" if has_location_signal else "수동", W["입지"],
                                "카카오 좌표·도보분" if has_location_signal else "free-text 키워드"))

    # 전세호가: HOLD 면 진위 평가(전세=레버리지·현금흐름). 라이브 수집=수동, 미수집=없음(전세수요 임퓨트 노출).
    if "전세호가" in W:
        comps.append(TrustComponent("전세호가", "수동" if c.jeonse_krw else "없음", W["전세호가"],
                                    "네이버 라이브 전세호가" if c.jeonse_krw
                                    else "전세호가 미수집 — 전세수요 표본평균 임퓨트(독립확인 필요)"))

    score = round(sum(_LEVEL_VALUE[k.level] * k.weight for k in comps) * 100, 1)
    blocking = tuple(f"{k.field}({k.level})" for k in comps
                     if _LEVEL_VALUE[k.level] < 1.0 and k.weight >= 0.15)
    # ★동명단지 근본해(2026-06-07): candidate_name 을 [구]name 으로 — ts_by_name 키 충돌(양천·구로 서울가든) 방지.
    gu = c.district.split()[-1] if c.district else ""
    cname = f"[{gu}]{c.listing.complex_name}" if gu else c.listing.complex_name
    return TrustScore(candidate_name=cname, components=tuple(comps),
                      score_pct=score, grade=_grade(score), blocking=blocking)
