"""금융 계산 결정론 테스트."""
from agent_realestate.analysts.finance import (build_finance_plan,
                                              compute_acquisition_tax,
                                              compute_comprehensive_tax,
                                              compute_dsr_loan, compute_ltv_loan)

EOK = 100_000_000


def test_ltv():
    assert compute_ltv_loan(830_000_000, 0.70) == 581_000_000


def test_acq_tax_brackets():
    # ≤6억 1.0% (+교육세 0.1 → 1.1%)
    assert abs(compute_acquisition_tax(500_000_000, False, 59) - int(500_000_000 * 0.011)) < 5
    # 9억 초과 3.0% (+교육세) — 85㎡ 이하
    t9 = compute_acquisition_tax(1_000_000_000, False, 84)
    assert abs(t9 - int(1_000_000_000 * 0.033)) < 5
    # 생애최초 감면 -200만
    t_first = compute_acquisition_tax(830_000_000, True, 59)
    t_no = compute_acquisition_tax(830_000_000, False, 59)
    assert t_no - t_first == 2_000_000


def test_comprehensive_tax():
    # ≤10억 1주택 → 공시가≈6.5억 < 12억 공제 → 종부세 0
    assert compute_comprehensive_tax(1_000_000_000, num_homes=1) == 0
    assert compute_comprehensive_tax(830_000_000, num_homes=1) == 0
    # 25억 1주택 → 공시가≈16.25억 > 12억 → 과표 발생, 종부세 >0
    assert compute_comprehensive_tax(2_500_000_000, num_homes=1) > 0
    # 다주택(공제 9억)은 같은 가격에서 더 큼
    assert (compute_comprehensive_tax(2_000_000_000, num_homes=2)
            >= compute_comprehensive_tax(2_000_000_000, num_homes=1))


def test_plan_has_comprehensive_tax():
    p = build_finance_plan(price_krw=830_000_000, ltv_ratio=0.70, annual_income_krw=100_000_000,
                           own_capital_krw=420_000_000, rate=0.043, term_years=40,
                           first_time=True, area_exclusive_m2=59)
    assert p.comprehensive_tax_krw == 0   # ≤12억


def test_dsr_loan_positive_and_bounded():
    loan = compute_dsr_loan(100_000_000, rate=0.043, term_years=40, dsr_limit=0.40)
    assert loan > 0
    # 연 4천만 상환여력으로 40년 4.3% → 대략 8억 안팎, LTV(7억)보다 큼
    assert 700_000_000 < loan < 1_000_000_000


def test_dsr_loan_monthly_matches_bank_case():
    # 2026-06-08 검증 브리지: 은행 실무는 월 단위(360개월) 원리금균등 DSR.
    # 소득1억·금리7%(기준4%+스트레스3%)·30년 → 경향신문 실사례 5억100만원과 일치
    # (Wolfram 독립검증). 과거 연단위 근사(≈4.96억)로의 회귀 방지 lock.
    loan = compute_dsr_loan(100_000_000, rate=0.07, term_years=30, dsr_limit=0.40)
    assert 498_000_000 < loan < 504_000_000   # 5.010억 ±0.03


def test_plan_equity_ok():
    p = build_finance_plan(price_krw=830_000_000, ltv_ratio=0.70,
                           annual_income_krw=100_000_000, own_capital_krw=420_000_000,
                           rate=0.043, term_years=40, first_time=True, area_exclusive_m2=59)
    assert p.loan_krw == min(p.ltv_loan_krw, p.dsr_loan_krw)
    assert p.equity_ok is True            # 자기자본 4.2억 > 필요액
    assert p.equity_required_krw < 420_000_000
