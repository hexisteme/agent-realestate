"""금융 계산 — 전부 결정론 (LLM 계산 금지, G3). 모든 산출은 AGENT_CALC FACT.

glossary-real-estate: LoanHeadroom, EquityCapital, EquityGap.
정책 *수치*(LTV율·취득세 구간)는 코드 상수가 아니라 호출자가 PolicySnapshot 에서
확정해 넘긴다 (RDU-059). 아래 함수는 그 수치를 받아 *계산*만 한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..policy_params import PolicyParams

EOK = 100_000_000  # 1억
_P = PolicyParams()  # 기본값 (미검증) — 호출자가 from_cache 로 교체


@dataclass(frozen=True)
class FinancePlan:
    """매수 자금 구조 — 결정론 산출."""

    price_krw: int
    ltv_loan_krw: int
    dsr_loan_krw: int            # 기본 금리 DSR 한도 (표시용)
    dsr_loan_stress_krw: int     # stress DSR 한도 (P2-9 가산금리 적용, 실제 적용 한도)
    loan_krw: int            # min(LTV, stress DSR)
    loan_binding: str        # "LTV" | "DSR(stress)"
    acquisition_tax_krw: int
    broker_fee_krw: int
    equity_required_krw: int
    equity_ok: bool
    annual_interest_krw: int
    property_tax_krw: int
    comprehensive_tax_krw: int   # 종합부동산세 (③ 1주택 12억 공제, ≤12억은 0)


@dataclass(frozen=True)
class PolicyLoanCheck:
    """정책대출(저리) 적격 판정 — 부적격이면 사유. HOLD_AND_RENT·생애최초 사용자 맞춤."""
    program: str
    eligible: bool
    reason: str


def assess_policy_loans(*, annual_income_krw: int, price_krw: int,
                        first_time: bool = True, num_homes: int = 1,
                        has_newborn: bool = False) -> list["PolicyLoanCheck"]:
    """정책대출 적격 판정(2026 기준). 무주택·실수요 저리대출의 소득·주택가(·신생아) 요건 대비 판정.
    price_krw 는 *사용자 실제 타깃가*(대표값) 기준 — 최저가 아웃라이어로 과대적격 표시 방지.
    출처: 주택도시기금 디딤돌/신생아특례 · 한국주택금융공사(HF) 보금자리론 (확인 2026-06-05).
    부적격 사유를 명시해 '헛된 기대' 차단(사용자 맞춤 정보)."""
    inc, p = annual_income_krw / EOK, price_krw / EOK
    out: list[PolicyLoanCheck] = []

    def chk(prog, inc_cap_eok, price_cap_eok, need_newborn=False):
        fails = []
        if not (first_time and num_homes <= 1):
            fails.append("무주택 요건 미충족")
        if need_newborn and not has_newborn:
            fails.append("신생아(2년내 출산) 가구 아님/미기재")
        if inc > inc_cap_eok:
            fails.append(f"소득 {inc:.2f}억 > {inc_cap_eok}억")
        if p > price_cap_eok:
            fails.append(f"주택 {p:.1f}억 > {price_cap_eok}억")
        return PolicyLoanCheck(prog, not fails, "적격" if not fails else " · ".join(fails))

    # ★적대검증(2026-06-06, agent-intel + WebSearch 공식 교차확인): 디딤돌 *생애최초* 소득 = 7천만(0.85=8.5천만은 *신혼가구* 기준 오매핑).
    #   출처 myhome.go.kr/selectSteppingStoneLoanView · banksalad. (신혼가구 8.5천 별도 row 부재 = 알려진 gap, 리포트 기재.)
    out.append(chk("디딤돌(생애최초)", 0.70, 5.0))                       # 부부합산 ≤7천(생애최초·2자녀)·주택 ≤5억 (신혼=8.5천 별도)
    # ★적대검증(2026-06-06): 신생아 특례 주택가격 6억→9억 상향(확인 2026-06-06). 출처 hf.go.kr/sub01_02_01 · myhome.go.kr/selectBabySpecialCaseStepStoneLoneView.
    out.append(chk("신생아 특례(디딤돌)", 1.3, 9.0, need_newborn=True))   # 신생아 가구·부부합산 ≤1.3억(맞벌이 2억)·주택 시세 ≤9억
    out.append(chk("보금자리론", 0.7, 6.0))                             # 소득 ≤7천(신혼 8.5천)·주택 ≤6억
    return out


# ★공시가 현실화율 — 단일값(0.65) 대신 가격대 구간화(공동주택 2025 평균 ~69%, 고가일수록↑).
# 정확값은 부동산공시가격알리미 실측 우선 — 아래는 INFERENCE 근사(재산세·종부세 과표용).
def official_ratio_tiered(price_krw: int, base: float = 0.69) -> float:
    """공동주택 시세대비 공시가율 근사. ★적대검증(2026-06-06): 기존 9·15억 계단(0.68→0.69→0.72)은
    경계서 세금 불연속(15억서 매매 100만원에 세금 14.8% 한계율 점프=비현실). → piecewise-linear 연속화.
    저가 0.68 → 9억 0.685 → 15억 0.69 → 25억 0.72 선형 보간(현실화율은 고가일수록 완만↑·연속)."""
    eok = price_krw / EOK
    if eok <= 9:
        return 0.68
    if eok <= 15:
        return round(0.68 + (eok - 9) / (15 - 9) * (base - 0.68), 4)   # 9→0.68, 15→base(0.69)
    return round(min(0.72, base + (eok - 15) / (25 - 15) * (0.72 - base)), 4)  # 15→0.69, 25→0.72


def compute_broker_fee(price_krw: int) -> int:
    """매매 중개보수 — 한국 상한요율 구간(2021 개정). ★적대검증(2026-06-06): 기존 flat 0.4% 는
    9억 초과 구간을 과소계상(자기자본 과소→F_OVERBUDGET 게이트 거짓통과). 구간: 5천↓0.6%·5천~2억0.5%·
    2~9억0.4%·9~12억0.5%·12~15억0.6%·15억↑0.7%(상한, 실제는 협의). [사실] 공인중개사법 시행규칙 별표."""
    eok = price_krw / EOK
    rate = (0.006 if eok <= 0.5 else 0.005 if eok <= 2 else 0.004 if eok <= 9
            else 0.005 if eok <= 12 else 0.006 if eok <= 15 else 0.007)
    return int(price_krw * rate)


def compute_ltv_loan(price_krw: int, ltv_ratio: float) -> int:
    """LoanHeadroom (LTV) = 매매가 × LTV율. (방공제 등은 아파트엔 미적용)"""
    return int(price_krw * ltv_ratio)


def compute_dsr_loan(
    annual_income_krw: int,
    rate: float,
    term_years: int,
    dsr_limit: float = 0.40,
    existing_annual_debt_krw: int = 0,
) -> int:
    """DSR 한도 역산: 연 상환여력 = 소득×DSR - 기존부채상환. 그 연금(annuity)을
    원리금균등 현재가치로 환산 → 대출 가능 원금."""
    annual_capacity = annual_income_krw * dsr_limit - existing_annual_debt_krw
    if annual_capacity <= 0 or rate <= 0:
        return 0
    # ★월 단위 원리금균등(2026-06-08 검증): 은행 실무 DSR 은 월상환(360개월) 기준. 기존 연단위 근사는
    #   한도를 ~0.04~0.05억 과소평가했다(연/월 복리차). 검증: 소득1억·4%·30년 → 5.01억 = 경향신문 실사례
    #   5억100만원과 일치, Wolfram 독립검증. PV = PMT_월 × [1 - (1+r_월)^-n_월] / r_월.
    r_m = rate / 12
    n_m = term_years * 12
    pv = (annual_capacity / 12) * (1 - (1 + r_m) ** (-n_m)) / r_m
    return int(pv)


def compute_acquisition_tax(price_krw: int, first_time: bool, area_exclusive_m2: float,
                            params: PolicyParams = _P) -> int:
    """취득세(주택 유상취득). 세율·구간·감면은 PolicyParams(정책캐시) 에서 (RDU-059)."""
    eok = price_krw / EOK
    if eok <= params.acq_low_threshold_eok:
        rate = params.acq_low_rate
    elif eok <= params.acq_high_threshold_eok:
        rate = (eok * 2 / 3 - 3) / 100  # 6~9억 선형 (법정 산식)
    else:
        rate = params.acq_high_rate
    base = price_krw * rate
    edu = base * params.acq_edu_ratio
    farm = price_krw * params.acq_farm_rate if area_exclusive_m2 > params.acq_farm_area else 0
    total = base + edu + farm
    if first_time and eok <= params.acq_relief_cap_eok:
        total = max(0, total - params.acq_first_relief)
    return int(total)


def compute_property_tax(price_krw: int, params: PolicyParams = _P, num_homes: int = 1) -> int:
    """연 재산세 근사 — ★1세대1주택 특례 반영(3차 감사 D, 2026-06-11).
    1주택: 공정시장가액비율 특례(공시 3억↓43%·3~6억 44%·6억↑45%, 시행령 109조) +
    공시 9억 이하 특례세율(표준 −0.05%p, 지방세법 111조의2). 다주택: 표준 60%·표준세율.
    공시가율은 가격대 구간화(official_ratio_tiered, 공동주택 ~69%)."""
    official = price_krw * official_ratio_tiered(price_krw)
    if num_homes == 1:
        fmr = next(r for hi, r in params.fair_market_1home_tiers if official <= hi)
        brackets = (params.property_special_brackets if official <= 900_000_000
                    else params.property_tax_brackets)
    else:
        fmr, brackets = params.fair_market_ratio, params.property_tax_brackets
    base = official * fmr
    tax = _marginal_tax(base, brackets)                       # R11 데이터화
    tax += base * params.property_urban_rate                  # 도시지역분 근사
    return int(tax)


def _marginal_tax(base: float, brackets) -> float:
    """과표 base 에 marginal 누진 구간 적용 (상한, 한계세율) 리스트."""
    tax, lo = 0.0, 0.0
    for hi, rate in brackets:
        if base > lo:
            tax += (min(base, hi) - lo) * rate
            lo = hi
        else:
            break
    return tax


def compute_comprehensive_tax(price_krw: int, num_homes: int = 1,
                              params: PolicyParams = _P) -> int:
    """종합부동산세(연) — 공제·공시가율·공정시장가액비율은 PolicyParams. 1주택 누진.
    ≤공제(1주택 12억)면 0. 정책 변동 → 캐시 주입. 공시가율 구간화(official_ratio_tiered)."""
    official = price_krw * official_ratio_tiered(price_krw)
    deduction = params.jongbu_deduction_1home if num_homes == 1 else params.jongbu_deduction_multi
    base = max(0.0, official - deduction) * params.fair_market_ratio
    if base <= 0:
        return 0
    return int(_marginal_tax(base, params.jongbu_brackets))   # R11 데이터화


def _ltc_rate_1home(hold_years: float, resident_years: float) -> float:
    """1세대1주택 장기보유특별공제율 (소득세법 §95②, 5차 감사 H 정정 2026-06-14).
    조건: 보유 3년 이상 AND 거주 2년 이상. 미충족이면 0.
    공제율: 보유 min(hold_years,10)×4% + 거주 min(resident_years,10)×4%, 합산 최대 80%."""
    if hold_years < 3 or resident_years < 2:
        return 0.0
    return min(0.80, 0.04 * min(hold_years, 10) + 0.04 * min(resident_years, 10))


def compute_capital_gains_tax(buy_krw: int, sell_krw: int, hold_years: float,
                              is_one_home: bool = True, resident_years: float = 0,
                              params: PolicyParams = _P) -> int:
    """양도소득세 (R8, 간이). 1세대1주택 12억 이하 비과세 / 12억 초과 안분 / 장기보유특별공제 /
    단기(<2년) 중과 / 2년+ 누진. 필요경비·지방소득세는 단순화. LIVE_THEN_SELL 용.
    ★5차 감사 H(2026-06-14): 1세대1주택 장특공 조건 정정 — 보유 3년+ AND 거주 2년+ (기존: 거주 2년+ 단독)."""
    gain = max(0, sell_krw - buy_krw)
    if gain <= 0:
        return 0
    if is_one_home and sell_krw <= 1_200_000_000:        # 1세대1주택 12억 이하 비과세
        return 0
    taxable = gain
    if is_one_home and sell_krw > 1_200_000_000:         # 12억 초과 안분 과세
        taxable = gain * (sell_krw - 1_200_000_000) / sell_krw
    if is_one_home:
        ltc = _ltc_rate_1home(hold_years, resident_years)
    else:
        ltc = min(0.30, 0.02 * min(hold_years, 15))     # 다주택·일반: 보유만 2%/년, 최대 30%
    base = max(0.0, taxable * (1 - ltc) - 2_500_000)     # 기본공제 250만
    if base <= 0:
        return 0
    if hold_years < 1:
        return int(base * 0.70)                          # 단기 중과(주택 1년 미만)
    if hold_years < 2:
        return int(base * 0.60)                          # 1~2년
    brackets = [(14_000_000, 0.06), (50_000_000, 0.15), (88_000_000, 0.24), (150_000_000, 0.35),
                (300_000_000, 0.38), (500_000_000, 0.40), (1_000_000_000, 0.42), (float("inf"), 0.45)]
    return int(_marginal_tax(base, brackets))            # 2년+ 누진


def capital_gains_tax_schedule(
    buy_krw: int,
    sell_rate: float = 0.03,
    hold_years_list: tuple = (3, 5, 10),
    resident_years: float = 2.0,
    params: PolicyParams = _P,
) -> list[dict]:
    """장기보유특별공제 공제율 + 양도세 시나리오 테이블 — LIVE_THEN_SELL 보조(5차 감사 H 신규).
    sell_rate: 연 가격 상승률 가정(기본 3%). 반환: [{years, sell_krw, ltc_pct, cgt_krw}]"""
    rows = []
    for yrs in hold_years_list:
        sell = int(buy_krw * (1 + sell_rate) ** yrs)
        ltc = _ltc_rate_1home(yrs, resident_years)
        cgt = compute_capital_gains_tax(buy_krw, sell, yrs, True, resident_years, params)
        rows.append({"years": yrs, "sell_krw": sell, "ltc_pct": round(ltc * 100), "cgt_krw": cgt})
    return rows


def build_finance_plan(
    *,
    price_krw: int,
    ltv_ratio: float,
    annual_income_krw: int,
    own_capital_krw: int,
    rate: float,
    term_years: int,
    first_time: bool,
    area_exclusive_m2: float,
    dsr_limit: float = 0.40,
    existing_annual_debt_krw: int = 0,
    broker_fee_rate: float = 0.004,
    params: PolicyParams = _P,
    num_homes: int = 1,
    stress_addon: float | None = None,
    dsr_term_cap: int | None = None,
    loan_abs_cap_krw: int | None = None,
) -> FinancePlan:
    # ★DSR 만기 cap (6.27 대책): 수도권 주담대 만기 30년 제한 → 장기만기로 DSR 한도 부풀리기 차단.
    # 은행이 40년 제시해도 DSR 산정·실제만기 모두 30년 상한. regulated 후보에 dsr_term_cap=30 주입.
    eff_term = min(term_years, dsr_term_cap) if dsr_term_cap else term_years
    ltv_loan = compute_ltv_loan(price_krw, ltv_ratio)
    dsr_loan = compute_dsr_loan(annual_income_krw, rate, eff_term, dsr_limit, existing_annual_debt_krw)
    # stress DSR: 가산금리 적용 (P2-9). 실제 보수적 한도는 stress 쪽. ★수도권·규제지역 주담대는
    # 스트레스 하한 3%(10.15 대책, 6.27→10.15 상향), 비규제는 1.5% — stress_addon 으로 후보별 분리.
    addon = params.stress_rate_addon if stress_addon is None else stress_addon
    dsr_loan_stress = compute_dsr_loan(annual_income_krw, rate + addon,
                                       eff_term, dsr_limit, existing_annual_debt_krw)
    # ★3중 binding: min(LTV, stressDSR, 절대한도). 수도권/규제 주담대 절대한도 6억(6.27, 10.15: 15억초과
    # 차등 4억/2억). loan_abs_cap_krw 미주입(비규제)이면 무한대. binding 명시로 LTV 과대평가 차단.
    abs_cap = loan_abs_cap_krw if loan_abs_cap_krw else float("inf")
    loan = int(min(ltv_loan, dsr_loan_stress, abs_cap))
    binding = min(("LTV", ltv_loan), ("DSR(stress)", dsr_loan_stress),
                  ("한도(수도권6억)", abs_cap), key=lambda x: x[1])[0]
    acq = compute_acquisition_tax(price_krw, first_time, area_exclusive_m2, params)
    broker = compute_broker_fee(price_krw)   # ★중개보수 구간함수(2026-06-06 적대검증). broker_fee_rate param 은 legacy.
    equity_required = (price_krw - loan) + acq + broker
    return FinancePlan(
        price_krw=price_krw,
        ltv_loan_krw=ltv_loan,
        dsr_loan_krw=dsr_loan,
        dsr_loan_stress_krw=dsr_loan_stress,
        loan_krw=loan,
        loan_binding=binding,
        acquisition_tax_krw=acq,
        broker_fee_krw=broker,
        equity_required_krw=equity_required,
        equity_ok=equity_required <= own_capital_krw,
        annual_interest_krw=int(loan * rate),
        property_tax_krw=compute_property_tax(price_krw, params, num_homes),
        comprehensive_tax_krw=compute_comprehensive_tax(price_krw, num_homes, params),
    )
