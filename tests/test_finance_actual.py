"""정밀 모드(주담대상품·신용대출부담·금융기관확인고지) 테스트 — 2026-07-10 WS-2.
glossary-real-estate: MortgageProgram · CreditLoanBurden · RequiredEquity · FinanceConfirmNotice."""
from agent_realestate.analysts.finance import (
    FINANCE_CONFIRM_NOTICE,
    CreditLoan,
    build_finance_plan,
    build_finance_plan_actual,
    compute_credit_loan_annual_service,
    resolve_loan_abs_cap,
)


def test_credit_service_under_threshold_no_stress():
    # 총잔액 ≤1억 → 스트레스 가산 없음. 만기일시 5년 간주: 원금/5 + 이자
    svc = compute_credit_loan_annual_service([CreditLoan(50_000_000, 0.05)])
    assert svc == int(50e6 / 5 + 50e6 * 0.05)


def test_credit_service_over_threshold_stress():
    # 총잔액 1억 초과 → +1.5%p (3단계 스트레스 DSR)
    svc = compute_credit_loan_annual_service([CreditLoan(150_000_000, 0.05)])
    assert svc == int(150e6 / 5 + 150e6 * 0.065)


def test_credit_service_amortizing_uses_remaining_years():
    svc = compute_credit_loan_annual_service(
        [CreditLoan(60_000_000, 0.04, amortizing=True, remaining_years=3)])
    assert svc == int(60e6 / 3 + 60e6 * 0.04)


def test_abs_cap_tiers_1015():
    assert resolve_loan_abs_cap(int(14e8)) == 600_000_000
    assert resolve_loan_abs_cap(int(16e8)) == 400_000_000
    assert resolve_loan_abs_cap(int(26e8)) == 200_000_000
    assert resolve_loan_abs_cap(int(14e8), regulated=False) is None


def test_newborn_area_cap_blocks():
    r = build_finance_plan_actual(price_krw=int(8e8), own_capital_krw=int(3e8),
                                  annual_income_krw=int(1.0e8), program_key="newborn",
                                  first_time=True, has_newborn=True, area_exclusive_m2=101.0)
    assert not r.eligible and "㎡" in r.reason and r.plan is None


def test_didimdol_price_cap_blocks():
    r = build_finance_plan_actual(price_krw=int(6e8), own_capital_krw=int(3e8),
                                  annual_income_krw=int(0.6e8), program_key="didimdol_first",
                                  first_time=True)
    assert not r.eligible and r.plan is None


def test_bank_standard_matches_legacy_plan():
    # 동일 파라미터·신용대출 0 → 기존 build_finance_plan 과 산출 일치 (회귀 앵커)
    legacy = build_finance_plan(price_krw=int(15e8), own_capital_krw=0,
                                annual_income_krw=120_000_000, rate=0.042, term_years=40,
                                ltv_ratio=0.40, first_time=False, area_exclusive_m2=84.0,
                                stress_addon=0.03, dsr_term_cap=30,
                                loan_abs_cap_krw=600_000_000)
    act = build_finance_plan_actual(price_krw=int(15e8), own_capital_krw=0,
                                    annual_income_krw=120_000_000, program_key="bank_standard",
                                    rate=0.042, term_years=40, area_exclusive_m2=84.0)
    assert act.eligible
    assert act.plan.loan_krw == legacy.loan_krw
    assert act.plan.equity_required_krw == legacy.equity_required_krw


def test_credit_loans_shrink_dsr_loan():
    base = build_finance_plan_actual(price_krw=int(12e8), own_capital_krw=int(8e8),
                                     annual_income_krw=int(1.2e8), rate=0.042)
    withc = build_finance_plan_actual(price_krw=int(12e8), own_capital_krw=int(8e8),
                                      annual_income_krw=int(1.2e8), rate=0.042,
                                      credit_loans=[CreditLoan(150_000_000, 0.055)])
    assert withc.plan.dsr_loan_stress_krw < base.plan.dsr_loan_stress_krw
    assert withc.plan.equity_required_krw >= base.plan.equity_required_krw
    assert withc.credit_annual_service_krw > 0


def test_policy_program_dsr_exempt_binding_not_dsr():
    # 정책모기지 = 스트레스 DSR 미적용 → binding 은 LTV 또는 상품캡 (DSR 아님)
    r = build_finance_plan_actual(price_krw=int(4.8e8), own_capital_krw=int(2e8),
                                  annual_income_krw=int(0.65e8), program_key="didimdol_first",
                                  first_time=True, area_exclusive_m2=59.0)
    assert r.eligible
    assert "DSR" not in r.plan.loan_binding


def test_notice_always_present():
    r = build_finance_plan_actual(price_krw=int(9e8), own_capital_krw=int(4e8),
                                  annual_income_krw=int(1e8))
    assert "금융기관" in r.confirm_notice
    assert r.confirm_notice == FINANCE_CONFIRM_NOTICE
