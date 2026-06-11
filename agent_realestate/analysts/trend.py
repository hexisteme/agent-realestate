"""실거래 시계열 → 시세 추세. 전부 실데이터의 결정론 변환 (FACT).

설계 갱신 (2026-05-29, council deep 20260529-070023-dcb765 8인 수렴):
  '첫 거래↔마지막 거래' 2점 CAGR 점추정을 시나리오 중심값으로 복리 외삽하던 방식을 폐기.
  근거(검증됨): n=5 에서 마지막 1건이 흔들리면 15년 외삽이 48억↔99억(2배)으로 요동치고
  게이트 통과여부까지 불연속 — 점추정을 신뢰구간으로 위장한 '확신 생성 기계'.
  대체: log-선형 OLS 회귀로 *전체* 시계열의 연성장률을 추정하고 그 표준오차로 95% 신뢰구간을
  낸다. 시나리오는 점이 아니라 *밴드*(lo·mid·hi)로 표현 — 밴드 폭이 곧 불확실성의 정직한 말.
  + turing leave-one-out: 가장 오래된 1건을 빼고 재추정해 추세 안정성을 반증 테스트.
  관측(과거)=FACT, 외삽(미래)='추세 지속 시' 조건부 가정으로 *물리적으로 분리*.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# n<3 회귀 불가 시 신뢰구간 z. n>=3 은 t(0.975, df=n-2) 근사값을 사용.
_T_975 = {3: 4.303, 4: 3.182, 5: 2.776, 6: 2.571, 7: 2.447, 8: 2.365,
         9: 2.306, 10: 2.262, 11: 2.228, 12: 2.201}


@dataclass(frozen=True)
class PriceTrend:
    n: int                      # 거래 건수
    first_ym: str
    last_ym: str
    last_price_krw: int
    change_pct_total: float     # 첫 거래 대비 최근 거래 변동 % (참고)
    cagr: float                 # 2점(첫↔끝) CAGR — 참고용. 더 이상 시나리오 중심값 아님.
    growth_annual_pct: float    # log-OLS 회귀 연성장률 점추정 (%/년)
    growth_lo_pct: float        # 회귀 연성장률 95% CI 하한
    growth_hi_pct: float        # 회귀 연성장률 95% CI 상한
    r_squared: float            # 회귀 적합도
    loo_growth_pct: float | None  # leave-one-out(최古 1건 제외) 연성장률 — 추세 안정성 반증
    strength: str               # "강"|"중"|"약" — 의사결정 사용 가능 강도
    reliable: bool              # 하위호환: strength in ("강","중")
    note: str

    @property
    def band(self) -> tuple[float, float, float] | None:
        """시나리오용 연성장률 밴드 (lo, mid, hi) — 분수. strength=='약'이면 None
        (외삽 근거 부족 → 호출자가 보수 중립 밴드로 대체)."""
        if self.strength == "약":
            return None
        lo = max(-0.05, self.growth_lo_pct / 100)
        return (round(lo, 4), round(self.growth_annual_pct / 100, 4),
                round(self.growth_hi_pct / 100, 4))


def _ym_to_years(ym: str) -> float:
    y, m = ym.split("-")
    return int(y) + (int(m) - 1) / 12


def _log_ols(t: list[float], ly: list[float]) -> tuple[float, float, float]:
    """log(price) = a + b·t 회귀. (연성장률, slope SE, R²) 반환. n>=3 전제."""
    n = len(t)
    tb = sum(t) / n
    yb = sum(ly) / n
    sxx = sum((x - tb) ** 2 for x in t)
    syy = sum((z - yb) ** 2 for z in ly)
    sxy = sum((x - tb) * (z - yb) for x, z in zip(t, ly))
    if sxx <= 0:                                  # 같은 달에 거래 몰림 → 기울기 정의 불가
        return 0.0, float("inf"), 0.0
    b = sxy / sxx
    resid_ss = max(0.0, syy - b * sxy)
    s2 = resid_ss / (n - 2) if n > 2 else 0.0
    se_b = math.sqrt(s2 / sxx) if s2 > 0 else 0.0
    r2 = (sxy * sxy) / (sxx * syy) if syy > 0 else 0.0
    return b, se_b, r2


def _classify(n: int, ci_width_pp: float, loo_delta_pp: float, growth_pct: float) -> str:
    """의사결정 사용 강도. 표본·CI폭·LOO안정성·과열 종합 (council turing/gates 게이트).

    과열 임계 12%/년: 어떤 주거자산도 15년간 연 12%+ 복리로 지속 상승하지 않는다(평균회귀).
    근시 모멘텀을 장기 외삽 근거로 오용하는 것을 차단 — council musk/jobs '관측 추세 only'.
    """
    if n < 5 or ci_width_pp > 20.0 or loo_delta_pp > 5.0 or abs(growth_pct) > 12.0:
        return "약"
    if n >= 8 and ci_width_pp <= 8.0 and loo_delta_pp <= 3.0:
        return "강"
    return "중"


def compute_trend(series: list[dict]) -> PriceTrend | None:
    """series: [{deal_ym, price_krw}]. 2건 미만이면 None. log-OLS 회귀 + 95% CI + LOO."""
    if len(series) < 2:
        return None
    s = sorted(series, key=lambda r: r["deal_ym"])
    first, last = s[0], s[-1]
    n = len(s)
    chg = (last["price_krw"] - first["price_krw"]) / first["price_krw"] * 100
    span_years = max(1 / 12, _ym_to_years(last["deal_ym"]) - _ym_to_years(first["deal_ym"]))
    cagr = ((last["price_krw"] / first["price_krw"]) ** (1 / span_years) - 1) * 100

    t = [_ym_to_years(r["deal_ym"]) for r in s]
    ly = [math.log(r["price_krw"]) for r in s]

    if n < 3:                                     # 회귀 불가 → 2점 그대로, 약
        growth = cagr
        lo = hi = growth
        r2 = 0.0
        loo = None
        ci_width = float("inf")
        loo_delta = float("inf")
    else:
        b, se_b, r2 = _log_ols(t, ly)
        growth = (math.exp(b) - 1) * 100
        tcrit = _T_975.get(n, 1.96)
        lo = (math.exp(b - tcrit * se_b) - 1) * 100
        hi = (math.exp(b + tcrit * se_b) - 1) * 100
        ci_width = hi - lo
        # turing leave-one-out: 가장 오래된 1건 제외 재추정 (남은 n-1>=3 일 때만)
        if n - 1 >= 3:
            b2, _, _ = _log_ols(t[1:], ly[1:])
            loo = round((math.exp(b2) - 1) * 100, 2)
            loo_delta = abs(loo - growth)
        else:
            loo = None
            loo_delta = float("inf")

    strength = _classify(n, ci_width, loo_delta, growth)
    reliable = strength in ("강", "중")

    badge = {"강": "", "중": " ⚠️중강도(표본/구간 보통)", "약": " ⚠️약함(n<5·구간넓음·LOO불안정·과열 중)"}[strength]
    ci_str = (f", 95%CI [{lo:+.1f}~{hi:+.1f}%]" if n >= 3 else "")
    loo_str = (f", LOO {loo:+.1f}%(Δ{abs(loo-growth):.1f}%p)" if loo is not None else "")
    note = (f"실거래 {n}건 ({first['deal_ym']}→{last['deal_ym']}), "
            f"{first['price_krw']/1e8:.2f}억→{last['price_krw']/1e8:.2f}억 "
            f"(누적 {chg:+.1f}%). 회귀 연성장 {growth:+.1f}%/년{ci_str}{loo_str}{badge}")
    return PriceTrend(
        n=n, first_ym=first["deal_ym"], last_ym=last["deal_ym"],
        last_price_krw=last["price_krw"], change_pct_total=round(chg, 1),
        cagr=round(cagr, 2), growth_annual_pct=round(growth, 2),
        growth_lo_pct=round(lo, 2), growth_hi_pct=round(hi, 2), r_squared=round(r2, 3),
        loo_growth_pct=loo, strength=strength, reliable=reliable, note=note)
