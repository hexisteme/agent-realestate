"""시나리오 계산 — 결정론 (RDU-063 Dual Exit + 장기보유 전세수익).

HOLD_AND_RENT(기본): 1년 거주 후 전세 전환 → 잔여대출·보유비용·N년 평가차익.
LIVE_THEN_SELL: 손익분기 매도가 + 상승률.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HoldRow:
    years: int
    appreciation: float
    value_krw: int
    unrealized_gain_krw: int
    cumulative_carry_krw: int
    net_krw: int            # 평가차익 - 누적보유비용 (미실현, 매도 안 함)


@dataclass(frozen=True)
class HoldScenario:
    residual_loan_krw: int   # 전세 전환 후 잔여대출 (loan - jeonse, 0 floor)
    annual_carry_krw: int    # 연 보유비용 = 잔여대출 이자 + 재산세
    extra_cash_to_convert_krw: int  # 전세<대출 시 전환에 더 필요한 현금 (음수면 여유)
    rows: list[HoldRow]
    band_kind: str = "보수중립"   # "회귀밴드"(추세 지속 가정, 회귀 95%CI) | "보수중립"(추세 약함/없음)
    band_pct: tuple[float, float, float] = (0.0, 0.0, 0.0)  # 적용 연성장 (lo, mid, hi) %


# 추세가 약하거나 없을 때의 보수 중립 밴드 (낙관 편향 제거: 0 중심 대칭)
_NEUTRAL_BAND = (-0.02, 0.0, 0.02)

# ★장기 지속가능 연성장 상한(±4%/년) — §5.2(assembler 장기 캡)와 동일 원칙(3차 감사 2026-06-11:
#   §6 보유 시나리오가 단기 회귀밴드를 10/15년 무캡 복리해 §5.2 와 같은 단지 15년 가치가 2~3배
#   불일치 + '+25억' 류 비현실 외삽). 10년 이상 행에만 적용 — 5년 행은 단기 모멘텀 원본 유지.
_SUSTAIN_CAP = 0.04
_CAP_FROM_YEARS = 10


def compute_hold(
    *, price_krw: int, loan_krw: int, jeonse_krw: int, property_tax_krw: int,
    rate: float, comprehensive_tax_krw: int = 0,
    management_fee_annual_krw: int = 0, maintenance_annual_krw: int = 0,
    monthly_rent_krw: int = 0, vacancy_pct: float = 0.0,
    growth_band: tuple[float, float, float] | None = None,
    years_list=(5, 10, 15),
) -> HoldScenario:
    """장기보유 시나리오 — 미래 가치는 *점예측이 아니라 밴드*로 (council 20260529 수렴).

    growth_band: 회귀 연성장률 95%CI (lo, mid, hi) 분수. None 이면 추세가 약하거나 없는 것 →
    보수 중립 밴드(-2/0/+2%)로 대체하고 band_kind='보수중립'. 어느 경우든 미래 행은
    [가정] 조건부이며, 밴드 폭이 외삽 길이에 따라 벌어지는 것 자체가 불확실성의 표현이다.
    """
    residual = max(0, loan_krw - jeonse_krw)
    rent_income = int(monthly_rent_krw * 12 * (1 - vacancy_pct))
    # 연 보유비용 = 잔여대출 이자 + 재산세 + 종부세 + 관리비 + 수선 − 월세수익(반전세 시)
    annual_carry = int(residual * rate + property_tax_krw + comprehensive_tax_krw
                       + management_fee_annual_krw + maintenance_annual_krw - rent_income)
    extra = loan_krw - jeonse_krw
    if growth_band is not None:
        band = tuple(sorted(growth_band))         # (lo, mid, hi) 보장
        band_kind = "회귀밴드"
    else:
        band = _NEUTRAL_BAND
        band_kind = "보수중립"
    rows: list[HoldRow] = []
    for y in years_list:
        cum = annual_carry * y
        for a in band:
            a_eff = (max(-_SUSTAIN_CAP, min(_SUSTAIN_CAP, a))
                     if y >= _CAP_FROM_YEARS else a)
            value = int(price_krw * (1 + a_eff) ** y)
            gain = value - price_krw
            rows.append(HoldRow(years=y, appreciation=a_eff, value_krw=value,
                                unrealized_gain_krw=gain, cumulative_carry_krw=cum,
                                net_krw=gain - cum))
    return HoldScenario(residual_loan_krw=residual, annual_carry_krw=annual_carry,
                        extra_cash_to_convert_krw=extra, rows=rows,
                        band_kind=band_kind,
                        band_pct=(round(band[0] * 100, 1), round(band[1] * 100, 1),
                                  round(band[2] * 100, 1)))


@dataclass(frozen=True)
class NetWorthPath:
    """비교셋-초월(A′ vs B) 15년 순자산 단일화폐 투영 (설계 §6).

    상대노출은 *같은 비교셋 안에서만* 유효하므로, 서로 다른 생활권 비교는 결정론 재무 투영으로만.
    g(상승률)는 점추정 아닌 3-시나리오 밴드 — 보수 0% / 중립 물가(CPI) / 낙관 *그 생활권* base-rate
    median. ★낙관 g 는 그 생활권 것만 사용(전이 금지) — base-rate 미주입이면 중립으로 캡."""

    horizon: int
    name: str
    saenghwalgwon: str
    g_band_pct: tuple[float, float, float]   # (보수, 중립, 낙관) %/년
    sale_lo_krw: int
    sale_mid_krw: int
    sale_hi_krw: int
    residual_debt_krw: int
    cumulative_carry_krw: int
    opportunity_cost_krw: int
    net_lo_krw: int                          # 15년 후 순자산(미실현, 가정 매각가치 기준)
    net_mid_krw: int
    net_hi_krw: int
    legal_status: str
    baserate_injected: bool                  # 낙관 g 가 실제 생활권 base-rate 인지(아니면 중립 캡)


def project_networth_15yr(
    *, name: str, saenghwalgwon: str, price_krw: int, residual_debt_krw: int,
    annual_carry_krw: int, equity_krw: int, base_rate_median: float | None,
    opportunity_rate: float = 0.035, cpi: float = 0.023, horizon: int = 15,
    legal_status: str = "PASS",
) -> NetWorthPath:
    """15년 순자산 = 가정 매각가치 − 잔여대출 − 누적 순보유비용 − 기회비용 (결정론·미실현).

    누적 순보유비용 = annual_carry × horizon (annual_carry 는 이미 임대수익 차감 net).
    기회비용 = 자기자본 × ((1+opportunity_rate)^horizon − 1) — ★복리(3차 감사 E, 2026-06-11):
    매각가치가 복리인데 기회비용만 단리면 15년에서 ~29% 과소(자기자본 3억 → 0.45억) = 낙관 편향.
    (break_even 은 years=1 이라 단/복리 무차별 — 일관성 주장은 기각됨.)
    낙관 g = base_rate_median(그 생활권). None(미주입/표본부족)이면 중립(CPI)으로 캡 — 전이 금지."""
    g_lo, g_mid = 0.0, cpi
    injected = base_rate_median is not None
    g_hi = base_rate_median if injected else cpi
    carry = annual_carry_krw * horizon
    opp = int(equity_krw * ((1 + opportunity_rate) ** horizon - 1))

    def _sale(g: float) -> int:
        return int(price_krw * (1 + g) ** horizon)

    def _net(g: float) -> int:
        return _sale(g) - residual_debt_krw - carry - opp

    return NetWorthPath(
        horizon=horizon, name=name, saenghwalgwon=saenghwalgwon,
        g_band_pct=(round(g_lo * 100, 1), round(g_mid * 100, 1), round(g_hi * 100, 1)),
        sale_lo_krw=_sale(g_lo), sale_mid_krw=_sale(g_mid), sale_hi_krw=_sale(g_hi),
        residual_debt_krw=residual_debt_krw, cumulative_carry_krw=carry, opportunity_cost_krw=opp,
        net_lo_krw=_net(g_lo), net_mid_krw=_net(g_mid), net_hi_krw=_net(g_hi),
        legal_status=legal_status, baserate_injected=injected)


@dataclass(frozen=True)
class BreakEven:
    costs_krw: int
    break_even_price_krw: int
    break_even_rate: float
    capital_gains_tax_5pct_krw: int  # R8: +5%/년 매도 시 예상 양도세(1세대1주택 거주2년+ 가정)


def compute_break_even(
    *, price_krw: int, acquisition_tax_krw: int, broker_fee_krw: int,
    annual_interest_krw: int, property_tax_krw: int, equity_krw: int,
    years: int = 1, opportunity_rate: float = 0.035,
    is_one_home: bool = True, resident_years: float = 1,
) -> BreakEven:
    from ..analysts.finance import compute_capital_gains_tax
    # ★5차 감사(2026-06-14): 기회비용 복리 통일 — project_networth_15yr 와 일관성.
    # 단리(rate×years) vs 복리 차이: 1년=0, 3년=0.37%p. break_even years≤3이 전형이나 정합성 우선.
    costs = (acquisition_tax_krw + broker_fee_krw * 2
             + annual_interest_krw * years + property_tax_krw * years
             + int(equity_krw * ((1 + opportunity_rate) ** years - 1)))
    sell5 = int(price_krw * (1.05 ** years))
    cgt = compute_capital_gains_tax(price_krw, sell5, years, is_one_home, resident_years)
    return BreakEven(costs_krw=costs, break_even_price_krw=price_krw + costs,
                     break_even_rate=round(costs / price_krw, 4),
                     capital_gains_tax_5pct_krw=cgt)
