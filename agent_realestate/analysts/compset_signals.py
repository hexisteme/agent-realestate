"""검증된 3신호 결정론 코어 (재편 A, 2026-06-01).

FACT 백테스트가 살린 것만 담는다 (10축 가중점수 ranking 은 게이트 미통과로 폐기):
  1) 생활권 base-rate band  — between-생활권 실현 CAGR(구로 +7.0% vs 수성 +1.3%). 주신호.
  2) within-구 mean-reversion — 역발상 타이밍(저평가→반등여지). |ρ|0.3~0.5 stationary. 품질 아님.
  3) 적합도 facts            — 예산·유동성·전세가율·경사 등 결정론 사실(점수 아님).

전부 결정론(G3): 동일 입력 → 동일 산출. 미래 점예측 금지(RDU-021).
입력은 *주입 테이블*(MOLIT 선계산, --cagr/--location 과 동일 패턴) — report-time 네트워크 없음.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass


# ── 1) 생활권 base-rate ────────────────────────────────────────────────
@dataclass(frozen=True)
class BaseRateBand:
    """생활권×전용band 의 과거 실현 CAGR 밴드 (between-생활권 주신호)."""

    saenghwalgwon: str          # 생활권 키 (예: "구로-신도림")
    area_band: int              # 전용 band (59/72/84/110/135)
    cagr_p25: float
    cagr_median: float
    cagr_p75: float
    n: int
    note: str = "동일단지 실현 11y [과거사실·미래보장 아님]"


def band_from_cagrs(cagrs: list[float]) -> tuple[float, float, float]:
    """CAGR 표본 → (p25, median, p75). 점예측 아닌 밴드."""
    if not cagrs:
        raise ValueError("base-rate 표본 없음")
    s = sorted(cagrs)
    return (s[len(s) // 4], statistics.median(s), s[(3 * len(s)) // 4])


def base_rate_band(saenghwalgwon: str, area_band: int, cagrs: list[float],
                   n_min: int = 8) -> BaseRateBand | None:
    """표본이 n_min 미만이면 None (필터only 라벨로 강등 — 편향-분산 가드)."""
    if len(cagrs) < n_min:
        return None
    p25, med, p75 = band_from_cagrs(cagrs)
    return BaseRateBand(saenghwalgwon, area_band, round(p25, 4),
                        round(med, 4), round(p75, 4), len(cagrs))


# ── 2) within-구 mean-reversion (역발상 타이밍) ─────────────────────────
@dataclass(frozen=True)
class MeanReversionSignal:
    """within-생활권 상대 최근 CAGR 백분위 → 저평가/고평가. 역발상(저평가=반등여지)."""

    complex_name: str
    rel_recent_cagr: float      # 생활권 평균 대비 최근 CAGR 편차
    percentile: float           # 0~1 (낮을수록 최근 덜 오름=저평가)
    label: str                  # 저평가(반등여지)/중립/고평가(과열주의)
    note: str = ("역발상 타이밍(mean-reversion |ρ|0.3~0.5 stationary) — "
                 "구조적 품질 아님 · 장기 단일보유엔 상쇄로 약함")


def classify_mean_reversion(percentile: float) -> str:
    """mean-reversion(부호 음): 최근 덜 오른(저백분위) 단지가 반등여지.
    하위 1/3 → 저평가(반등여지) / 상위 1/3 → 고평가(과열주의) / 중간 → 중립."""
    if percentile <= 1 / 3:
        return "저평가(반등여지)"
    if percentile >= 2 / 3:
        return "고평가(과열주의)"
    return "중립"


def mean_reversion_signal(complex_name: str, recent_cagr: float,
                          peer_recent_cagrs: list[float]) -> MeanReversionSignal | None:
    """peer = 같은 생활권 단지들의 최근 CAGR. 그 안에서 이 단지의 상대 위치."""
    peers = [c for c in peer_recent_cagrs if c is not None]
    if len(peers) < 4:               # peer 부족 → 신호 억제
        return None
    mean = statistics.mean(peers)
    below = sum(1 for c in peers if c < recent_cagr)
    pct = below / len(peers)
    return MeanReversionSignal(complex_name, round(recent_cagr - mean, 4),
                               round(pct, 3), classify_mean_reversion(pct))


# ── 2b) 시계열 mean-reversion (구 장기추세 대비 저평가) — 검증된 타이밍 신호 ──────────
@dataclass(frozen=True)
class TrendGapSignal:
    """구 가격수준의 장기 로그선형 추세 대비 현재 잔차 → *시계열* mean-reversion 타이밍.
    검증(validate_reversion.py, 구패널 n=60): 추세대비 저평가→forward Spearman ρ=+0.68(시계열, p<0.001).
    ★단 횡단면(구 선택)은 regime-불안정(t0별 −0.46~+0.53) → '지금 그 구가 싼 시점인가' 타이밍이지
    '어느 구가 더 오르나' 선택 신호 아님. 독립 거시사이클 N≈2.5 = 저검정력(한 사이클 더면 약해질 수 있음)."""

    gu: str
    gap_pct: float       # (추세적합가 − 실제가)/추세적합가 ×100. 양수=추세 아래=저평가(반등여지)
    z: float             # 잔차(log) 표준화 — 역사적 변동 대비 현재 이탈 폭
    label: str
    n: int               # 추세 적합에 쓴 연수
    note: str = "구 84밴드 연median 로그선형 추세 대비 — 시계열 타이밍(검증 ρ+0.68·N≈2.5 저검정력)"


def classify_trend_gap(gap_pct: float) -> str:
    """추세 대비: 아래(+) = 저평가(반등여지) / 위(−) = 고평가(과열주의). ±5% 경계."""
    if gap_pct >= 5.0:
        return "저평가(추세하회·반등여지)"
    if gap_pct <= -5.0:
        return "고평가(추세상회·과열주의)"
    return "중립(추세부근)"


def trend_gap_signal(gu: str, gap_pct: float, z: float, n: int) -> TrendGapSignal:
    return TrendGapSignal(gu, round(gap_pct, 1), round(z, 2), classify_trend_gap(gap_pct), n)


# ── 3) 적합도 facts (점수 아님) ────────────────────────────────────────
def fitness_facts(*, equity_required_krw: int, own_capital_krw: int,
                  units: int, jeonse_ratio: float | None,
                  slope_pct: float | None, legal_status: str) -> dict:
    """단지 적합도 = 결정론 사실 모음(매력도 점수 아님). 예산·법규는 하드게이트."""
    return {
        "예산적합": equity_required_krw <= own_capital_krw,
        "필요자기자본_억": round(equity_required_krw / 1e8, 2),
        "법적실행": legal_status,                       # PASS/조건부/FAIL
        "유동성_세대": units,
        "유동성_등급": ("상" if units >= 1500 else "중" if units >= 700 else "하"),
        "전세가율": round(jeonse_ratio, 3) if jeonse_ratio is not None else None,
        "경사_pct": slope_pct,
        "경사_등급": (None if slope_pct is None else
                    "완경사" if slope_pct <= 6 else "보통" if slope_pct <= 10 else "급경사"),
    }
