"""의사결정 아키텍처(v8 재포지셔닝 구현) — 예측기 아닌 PRIOR + binary 촉매 watchlist.

배경(2026-06-04 백테스트 definitive): 10축 점수는 실현 가치상승을 예측 못함(n=84 ρ=0.08, 95%CI ρ≥0.30 배제).
구-level forward 예측도 mean-reversion(−0.73)+비정상+검정력천장(구25<85)으로 불가. → 예측기 추구 종결.
대신 (a) 장기 구조 PRIOR(생활권 base-rate + 고용 코리도 근접) (b) binary 촉매 watchlist(≥2=강함)
(c) 하드필터로 '제로(정체) 회피' 게임. 본 모듈은 기존 Candidate 필드만으로 결정론 산출(G3, 새 수집 0).

근거: report/backtest/backtest_predictor-upgrade_20260604.html, report/audit/audit_identity-rearch_20260601.md, sage(gemma4) continuation 3047aa14.
"""
from __future__ import annotations
from ..domain import Candidate

# 고용 코리도 = 주요 업무지구 근접. cbd_km(강남·시청·여의도 최단) proxy 사용 — 판교·마곡·가산은 미반영(한계 명시).
EMP_NOTE = "고용 코리도 = 주요 업무지구(강남·여의도·시청) 근접도. cbd_km proxy(판교·마곡·가산 미반영)."


def employment_corridor(c: Candidate) -> int | None:
    """고용 코리도 근접 ordinal(0~2). cbd_km 미주입이면 None."""
    km = getattr(c, "cbd_km", None)
    if km is None:
        return None
    return 2 if km <= 4 else 1 if km <= 8 else 0


def catalyst_flags(c: Candidate) -> dict:
    """binary 촉매 watchlist — 기존 필드 기반. supply(공급파이프라인)는 데이터 미수집=None(정직).
    반환: {flags:{고용근접,교통,재건축조기}, supply:None, yes:int(≥2=강함)}."""
    km = getattr(c, "cbd_km", None)
    transit = c.transit or ""
    sub = (c.infra or {}).get("subway_m", 9999) if c.infra else 9999
    lvl = c.redev_stage.level if c.redev_stage and c.redev_stage.level else 0
    flags = {
        "고용근접": (km is not None and km <= 8),                       # 업무지구 8km 내
        "교통": ("GTX" in transit or "역세권" in transit or sub <= 500),  # GTX/역세권/지하철 500m
        "재건축조기": (2 <= lvl <= 4),                                   # 추진위~사업시행(조기 re-rating, v7)
        "개발호재": bool(getattr(c, "dev_catalyst", None)),               # 인근 대형부지 개발 등 외부 호재(출처 필수, 2026-06-07)
    }
    yes = sum(1 for v in flags.values() if v)
    return {"flags": flags, "supply": None, "yes": yes, "strong": yes >= 2}


def district_prior(c: Candidate, struct_rank: float | None = None) -> float:
    """장기 구조 PRIOR(0~5) — ★예측기 아님. 생활권 long-run base-rate '구조 서열'(struct_rank) + 고용 코리도 blend.
    struct_rank = 그 후보의 생활권 base-rate median 이 비교풀 안에서 갖는 percentile(0~1, calibration-free 상대 서열).
    고용 0~2 → 0~1, blend 0.65:0.35 → [2,5] 스케일(타 축과 정합).

    ★적대검증(2026-06-06, sage gpt-oss:120b-cloud + gemma4:31b-cloud cross-family 만장일치 + steelman 불변):
      기존 base = gu_cagr(구 15년 trailing CAGR)의 양(+) 매핑은, scoring 이 상승여력서 *제거*한 바로 그 신호
      (validate_reversion: 구 모멘텀 forward ρ=−0.69 역상관·mean-revert ρ=−0.73·비정상)를 §0 PRIOR(의사결정 1차
      스크린)에 재유입시켜 매수자의 첫 정렬을 forward 기준 *역방향*으로 돌렸다(모듈 간 정합성 결함). →
      base 를 시스템 자체의 designated 구조신호(생활권 long-run base-rate median)의 *풀-내 percentile*로 교체.
      percentile 은 절대앵커(6.0~10.5%) 미사용 = 표본 calibration 위험 없는 순수 상대 '구조 서열'.
    ★struct_rank 미주입(base-rate 부재·풀<3)이면 중립 0.5 — trailing gu_cagr 의 역신호는 PRIOR 방향성에
      더 이상 쓰지 않는다(gu_cagr 는 §0 '구 base-rate' 표시 컬럼에 맥락으로만 보존).
    ★뒤집힐 조건: 생활권 base-rate median 이 forward 수익과 양(+) IC 를 큰 표본서 못 보이거나 percentile 이
      표본 mix 에 과민하면 employment-only(struct 비중 0)로 후퇴."""
    base = 0.5 if struct_rank is None else max(0.0, min(1.0, struct_rank))
    emp = employment_corridor(c)
    empn = 0.5 if emp is None else emp / 2.0
    return round(2.0 + 3.0 * (0.65 * base + 0.35 * empn), 2)


def liquidity_tailwind(c: Candidate, candidates: list[Candidate], regime_active: bool) -> dict:
    """단지별 거래량(실유동성) tailwind 플래그 (feature_discovery: 거래량은 바닥·상승진입 국면에
    forward수익 within-구 ρ≈+0.3, 과열기 소멸). 구내 거래량 백분위 + 현 국면 활성여부.
    ★regime_active=False(과열/조정)면 신호 비활성 — 단지 거래량은 표시하되 tailwind 미적용(정직)."""
    gu = c.district.split()[-1] if c.district else ""
    peers = [x.trade_annual for x in candidates
             if x.district.split()[-1] == gu and x.trade_annual is not None]
    ta = c.trade_annual
    if ta is None or len(peers) < 3:
        return {"level": "—", "pct": None, "tailwind": False}
    pct = sum(1 for v in peers if v <= ta) / len(peers)       # 구내 백분위(0~1)
    level = "상" if pct >= 0.67 else "중" if pct >= 0.33 else "하"
    # tailwind = 고거래량(상위) AND 현 국면 활성(바닥/상승진입). 과열기엔 비활성.
    return {"level": level, "pct": round(pct, 2), "trade_annual": ta,
            "tailwind": bool(regime_active and pct >= 0.67)}


def decision_summary(c: Candidate) -> dict:
    """후보 1건의 의사결정 아키텍처 요약(PRIOR + 촉매 + 라벨)."""
    cat = catalyst_flags(c)
    return {
        "prior": district_prior(c),
        "gu_cagr": getattr(c, "gu_cagr", None),
        "emp_corridor": employment_corridor(c),
        "catalysts": cat["flags"],
        "catalyst_yes": cat["yes"],
        "catalyst_strong": cat["strong"],
        "supply_known": False,
    }
