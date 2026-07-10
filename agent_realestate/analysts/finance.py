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


# ═══════════════════════════════════════════════════════════════════════════
# 정밀 모드 (2026-07-10, 사용자 요청) — 실매물가·실자기자본·주담대상품·기존 신용대출.
# glossary-real-estate: MortgageProgram(주담대상품) · CreditLoanBurden(신용대출부담) ·
# RequiredEquity(필요자기자본) · FinanceConfirmNotice(금융기관확인고지).
# ═══════════════════════════════════════════════════════════════════════════

FINANCE_CONFIRM_NOTICE = (
    "본 계산은 공개 정책 기준(출처·확인일 명시)의 결정론 시뮬레이션 추정치입니다. "
    "실제 대출 가능 금액·금리·승인 여부는 개인 신용도·소득 증빙·은행별 심사 기준에 따라 "
    "달라지므로 반드시 은행 등 금융기관에 직접 확인해야 합니다."
)


@dataclass(frozen=True)
class CreditLoan:
    """기존 신용대출 1건 — 잔액·연금리는 사용자 실제 값(추정 주입 금지, G1 정신).
    amortizing=False(만기일시)면 DSR 산정 시 5년 분할 간주(3단계 스트레스 DSR 운영 기준)."""

    balance_krw: int
    rate: float                    # 연 이자율 (예: 0.055 = 5.5%)
    amortizing: bool = False
    remaining_years: float = 5.0   # 분할상환(amortizing=True)일 때 실제 잔여만기

    def __post_init__(self) -> None:
        if self.balance_krw <= 0 or not (0 < self.rate < 0.5):
            raise ValueError("CreditLoan 잔액/금리 비정상 (잔액>0, 0<금리<0.5)")


def compute_credit_loan_annual_service(
    loans,
    *,
    stress_addon_credit: float = 0.015,
    stress_threshold_krw: int = 100_000_000,
) -> int:
    """CreditLoanBurden — 기존 신용대출의 DSR 산입 연 원리금(결정론 근사).
    산식: 만기일시 = 원금/5년 + 원금×(금리+가산) / 분할상환 = 원금/잔여만기 + 원금×(금리+가산).
    3단계 스트레스 DSR(2025-07~): 신용대출 '총잔액 1억 초과' 시에만 스트레스 가산(기본 1.5%p).
    [근사] 은행별 만기 간주·가산 적용 세부 상이 — FINANCE_CONFIRM_NOTICE 병기 의무.
    출처: 금융위 3단계 스트레스 DSR(korea.kr 정책브리핑 148943522) · KB/토스 해설 (확인 2026-07-10)."""
    loans = list(loans)
    if not loans:
        return 0
    total = sum(l.balance_krw for l in loans)
    addon = stress_addon_credit if total > stress_threshold_krw else 0.0
    svc = 0.0
    for l in loans:
        term = l.remaining_years if l.amortizing else 5.0
        svc += l.balance_krw / max(term, 1.0) + l.balance_krw * (l.rate + addon)
    return int(svc)


@dataclass(frozen=True)
class MortgageProgram:
    """주담대상품 — 자격·캡·금리범위를 데이터로(출처+확인일, RDU-059).
    ⚠️ 공표 기준 스냅샷 — 금리는 수시 변동, 캡·요건도 개편됨(rate_note 확인).
    dsr_exempt=True(정책모기지)는 은행 스트레스 DSR 미적용 대신 기금/공사 DTI·자산 심사가
    '별도로' 있음 — 본 엔진은 그 심사를 계산하지 않는다(정직 한계). 렌더 시
    FINANCE_CONFIRM_NOTICE 병기 의무."""

    key: str
    label: str
    price_cap_krw: int | None      # 주택가액 상한 (None=없음)
    income_cap_krw: int | None     # 부부합산 소득 상한
    loan_cap_krw: int | None       # 대출 원금 상한 (공표값 — 가구유형별 상이)
    ltv_cap: float | None          # 상품 LTV (None=호출자 규제 LTV 사용)
    rate_low: float
    rate_high: float
    dsr_exempt: bool               # 정책모기지 = 은행 스트레스 DSR 규제 미적용
    stress_exempt: bool
    requires_first_time: bool      # 무주택(생애최초/실수요) 요건
    requires_newborn: bool = False
    area_cap_m2: float | None = None
    rate_note: str = ""
    source: str = ""
    confirmed_date: str = ""


# 2026-07-10 웹 교차확인 스냅샷(hf.go.kr · myhome.go.kr · korea.kr · KB/뱅크샐러드 해설).
# loan_cap 은 공표 기준값 — 우대·가구유형(신혼·다자녀)별 상이 → 기금/은행 확인 필수.
MORTGAGE_PROGRAMS: dict[str, MortgageProgram] = {
    "didimdol_first": MortgageProgram(
        "didimdol_first", "디딤돌(생애최초)", 500_000_000, 70_000_000, 300_000_000, 0.70,
        0.0285, 0.0415, True, True, True,
        rate_note="금리 2.85~4.15%(2026-07 확인) — 우대금리 별도, 신혼·2자녀는 소득·가격캡 상이",
        source="https://www.hf.go.kr/ko/sub01/sub01_02_01.do", confirmed_date="2026-07-10"),
    "newborn": MortgageProgram(
        "newborn", "신생아 특례(디딤돌)", 900_000_000, 200_000_000, 500_000_000, 0.70,
        0.0180, 0.0450, True, True, True, requires_newborn=True, area_cap_m2=85.0,
        rate_note="특례금리 1.80~4.50%(2026-06-22 고시) — 소득구간별 차등",
        source="https://www.myhome.go.kr/hws/portal/cont/selectBabySpecialCaseStepStoneLoneView.do",
        confirmed_date="2026-07-10"),
    "bogeumjari": MortgageProgram(
        "bogeumjari", "보금자리론", 600_000_000, 70_000_000, 360_000_000, 0.70,
        0.0350, 0.0470, True, True, True,
        rate_note="금리 2026-07 고시 미확인(참고범위) — 공사 홈페이지·은행 확인 필수. 신혼 8.5천·1자녀 9천·다자녀 1억 소득캡 별도",
        source="https://www.hf.go.kr/ko/sub01/sub01_01_01.do", confirmed_date="2026-07-10"),
    "bank_standard": MortgageProgram(
        "bank_standard", "일반 은행 주담대", None, None, None, None,
        0.038, 0.050, False, False, False,
        rate_note="은행·신용도별 상이 — 반드시 실제 제시금리를 rate 로 입력",
        source="은행별 상이", confirmed_date="2026-07-10"),
}


def resolve_loan_abs_cap(price_krw: int, regulated: bool = True) -> int | None:
    """수도권·규제지역 주담대 절대한도 — 10.15 대책 가격 차등:
    15억 이하 6억 / 15억 초과~25억 4억 / 25억 초과 2억. 비규제 None(무제한).
    출처: korea.kr 10.15 주택시장 안정화 대책(2025-10-15 발표) (확인 2026-07-10)."""
    if not regulated:
        return None
    eok = price_krw / EOK
    if eok <= 15:
        return 600_000_000
    if eok <= 25:
        return 400_000_000
    return 200_000_000


@dataclass(frozen=True)
class ActualFinancePlan:
    """정밀 모드 산출 — 실매물가·실자기자본·주담대상품·기존 신용대출 반영.
    eligible=False 면 plan=None + 사유. 모든 표면 렌더에 confirm_notice 병기 의무."""

    program: MortgageProgram
    eligible: bool
    reason: str
    credit_annual_service_krw: int
    plan: FinancePlan | None
    confirm_notice: str = FINANCE_CONFIRM_NOTICE


def build_finance_plan_actual(
    *,
    price_krw: int,                      # 실매물가(라이브 호가 또는 실거래) — 추정가 금지(G1)
    own_capital_krw: int,
    annual_income_krw: int,
    program_key: str = "bank_standard",
    credit_loans=(),
    rate: float | None = None,           # 은행 실제 제시금리 — 미지정 시 상품 상단(보수)
    term_years: int = 30,
    first_time: bool = False,
    num_homes: int = 1,
    has_newborn: bool = False,
    area_exclusive_m2: float = 84.0,
    regulated: bool = True,
    ltv_ratio: float = 0.40,              # 일반 주담대 규제 LTV (정책상품은 상품 LTV 우선)
    dsr_limit: float = 0.40,
    stress_addon: float = 0.03,           # 수도권·규제 주담대 스트레스 하한 3%p(10.15)
    dsr_term_cap: int | None = 30,
    params: PolicyParams = _P,
) -> ActualFinancePlan:
    """정밀 자금 플랜(사용자 실입력) — ① 주담대상품 자격 판정(가격·소득·무주택·신생아·면적)
    ② 기존 신용대출 DSR 산입(CreditLoanBurden) ③ 대출 = min(상품/규제 LTV,
    스트레스 DSR(정책모기지 면제 시 제외), 절대한도(10.15 차등), 상품 대출캡).
    [결정론 G3] 동일입력→동일산출. [정직 한계] 정책모기지의 기금 DTI·자산심사는 미계산 —
    산출은 '공개 규정 기준 상한 추정'이며 실제 승인·금리는 FINANCE_CONFIRM_NOTICE 대로 확인."""
    prog = MORTGAGE_PROGRAMS[program_key]
    fails: list[str] = []
    if prog.requires_first_time and not (first_time and num_homes <= 1):
        fails.append("무주택(생애최초/실수요) 요건 미충족")
    if prog.requires_newborn and not has_newborn:
        fails.append("신생아(2년내 출산) 가구 아님/미기재")
    if prog.income_cap_krw is not None and annual_income_krw > prog.income_cap_krw:
        fails.append(f"소득 {annual_income_krw / EOK:.2f}억 > {prog.income_cap_krw / EOK:.1f}억")
    if prog.price_cap_krw is not None and price_krw > prog.price_cap_krw:
        fails.append(f"주택 {price_krw / EOK:.1f}억 > {prog.price_cap_krw / EOK:.0f}억")
    if prog.area_cap_m2 is not None and area_exclusive_m2 > prog.area_cap_m2:
        fails.append(f"전용 {area_exclusive_m2:.0f}㎡ > {prog.area_cap_m2:.0f}㎡")

    credit_svc = compute_credit_loan_annual_service(credit_loans)
    if fails:
        return ActualFinancePlan(prog, False, " · ".join(fails), credit_svc, None)

    rate_eff = rate if rate is not None else prog.rate_high   # 미지정 → 보수(상단)
    eff_term = min(term_years, dsr_term_cap) if dsr_term_cap else term_years
    ltv_eff = prog.ltv_cap if prog.ltv_cap is not None else ltv_ratio
    ltv_loan = compute_ltv_loan(price_krw, ltv_eff)
    dsr_loan = compute_dsr_loan(annual_income_krw, rate_eff, eff_term, dsr_limit, credit_svc)
    if prog.dsr_exempt:
        dsr_stress = dsr_loan                        # 표시용 — binding 후보에서 제외
        candidates = [("LTV", ltv_loan)]
    else:
        addon = 0.0 if prog.stress_exempt else stress_addon
        dsr_stress = compute_dsr_loan(annual_income_krw, rate_eff + addon, eff_term,
                                      dsr_limit, credit_svc)
        candidates = [("LTV", ltv_loan), ("DSR(stress)", dsr_stress)]
    abs_cap = resolve_loan_abs_cap(price_krw, regulated)
    if abs_cap is not None:
        candidates.append(("한도(10.15 차등)", abs_cap))
    if prog.loan_cap_krw is not None:
        candidates.append((f"상품캡({prog.label})", prog.loan_cap_krw))
    binding, loan = min(candidates, key=lambda x: x[1])
    loan = int(loan)
    acq = compute_acquisition_tax(price_krw, first_time, area_exclusive_m2, params)
    broker = compute_broker_fee(price_krw)
    equity_required = (price_krw - loan) + acq + broker
    plan = FinancePlan(
        price_krw=price_krw, ltv_loan_krw=ltv_loan, dsr_loan_krw=dsr_loan,
        dsr_loan_stress_krw=dsr_stress, loan_krw=loan, loan_binding=binding,
        acquisition_tax_krw=acq, broker_fee_krw=broker,
        equity_required_krw=equity_required,
        equity_ok=equity_required <= own_capital_krw,
        annual_interest_krw=int(loan * rate_eff),
        property_tax_krw=compute_property_tax(price_krw, params, num_homes),
        comprehensive_tax_krw=compute_comprehensive_tax(price_krw, num_homes, params),
    )
    return ActualFinancePlan(prog, True, "적격", credit_svc, plan)
