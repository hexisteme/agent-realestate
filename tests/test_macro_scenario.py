"""거시 전달 계산(blog/macro_scenario.py) — 산식 독립 검산(상각 루프·PV 항등식)·정책 산식 일치·문구 가드. 네트워크 없음."""
import json
import pytest
import re

from agent_realestate.analysts import finance as fin
from blog import macro_scenario as ms
from blog.wording_guard import assert_lead_wording_ok

CASES = ((300_000_000, 30, 4.50), (600_000_000, 40, 3.00), (100_000_000, 10, 6.00))


def _amortize_residual(P, rate_pct, years, pmt):
    bal, r = float(P), rate_pct / 100 / 12
    for _ in range(years * 12):
        bal = bal * (1 + r) - pmt
    return bal


def test_monthly_payment_matches_amortization_loop_three_cases():
    for P, y, r in CASES:
        m = ms.compute_monthly_payment(P, r, y)
        assert abs(_amortize_residual(P, r, y, m)) < 400 * y            # int 절사(≤1원/월)가 누적된 잔액 이내
        assert m == int(P * (r / 1200) / (1 - (1 + r / 1200) ** (-y * 12)))
    assert ms.compute_monthly_payment(120_000_000, 0.0, 10) == 1_000_000


def test_dsr_limit_and_payment_are_inverse():
    for P, y, r in CASES:
        lim = fin.compute_dsr_loan(100_000_000, r / 100, y)
        assert abs(ms.compute_monthly_payment(lim, r, y) - 100_000_000 * 0.4 / 12) <= 2


def test_scenario_table_shape_and_direction():
    rows = ms.build_scenario_table(300_000_000, 30, 4.5)
    assert [r["step_bp"] for r in rows] == list(ms.STEPS_BP)
    z = next(r for r in rows if r["step_bp"] == 0)
    assert z["delta_monthly_krw"] == 0 and z["monthly_krw"] == ms.compute_monthly_payment(300_000_000, 4.5, 30)
    up = next(r for r in rows if r["step_bp"] == 25)
    assert up["delta_monthly_krw"] == 44_887 and up["delta_annual_krw"] == 538_644        # Wolfram: 1,520,055 → 1,564,942
    assert ms.build_scenario_table(300_000_000, 40, 4.5)[2]["monthly_krw"] == 1_520_055        # 만기 40 → 30년 상한(6.27)


def test_dsr_table_uses_stress_and_cap():
    rows = ms.build_dsr_table(100_000_000, 4.5, 30, 1.5, cap_krw=None)
    assert rows[2]["stress_rate_pct"] == 6.0 and rows[2]["limit_krw"] == fin.compute_dsr_loan(100_000_000, 0.06, 30)
    assert rows[0]["limit_krw"] > rows[2]["limit_krw"] > rows[4]["limit_krw"] and rows[2]["delta_krw"] == 0
    capped = ms.build_dsr_table(100_000_000, 4.5, 30, 1.5, cap_krw=300_000_000)
    assert all(r["limit_krw"] <= 300_000_000 for r in capped) and capped[0]["capped"] is True


def test_breakeven_arithmetic():
    assert ms.compute_breakeven(4.74, 4.38) == {"gap_pp": 0.36, "steps_25bp": 2}
    assert ms.compute_breakeven(4.50, 4.50) == {"gap_pp": 0.0, "steps_25bp": 0}
    assert ms.compute_breakeven(4.00, 4.38)["steps_25bp"] == 0
    assert ms.compute_breakeven(4.75, 4.50) == {"gap_pp": 0.25, "steps_25bp": 1}


def test_holding_tax_from_official_matches_finance_formulas():
    for price in (800_000_000, 1_500_000_000, 2_500_000_000):
        official = int(price * fin.official_ratio_tiered(price))
        for homes in (1, 2):
            t = ms.compute_holding_tax_from_official(official, num_homes=homes)
            assert t["property_tax_krw"] == fin.compute_property_tax(price, num_homes=homes)
            assert t["jongbu_tax_krw"] == fin.compute_comprehensive_tax(price, num_homes=homes)
    assert ms.compute_holding_tax_from_official(1_100_000_000)["jongbu_tax_krw"] == 0
    assert ms.compute_holding_tax_from_official(1_300_000_000)["jongbu_tax_krw"] > 0


def test_holding_tax_table_uses_multiple():
    rows = ms.build_holding_tax_table(prices_eok=(20,), multiples=(1.50, 1.65))
    assert rows[0]["official_eok"] == 13.33 and rows[1]["official_eok"] == 12.12
    assert rows[0]["total_krw"] > rows[1]["total_krw"] > 0


def test_calc_page_wording_and_content():
    page = ms.render_calc_page(None, "2026-09-07", stress_pp=1.5)
    assert_lead_wording_ok(re.sub(r"<[^>]+>", " ", page), "calc.html")
    assert "관측값 미수집" in page and 'id=pay-rows' in page and "function pmt" in page and "None" not in re.sub(r"<[^>]+>", " ", page)
    ctx = {"cards": [{"code": "cofix_new", "label": "코픽스 신규취급액", "value_txt": "3.18%", "date": "2026-08-18"}]}
    page2 = ms.render_calc_page(ctx, "2026-09-07", stress_pp=1.5)
    assert "관측값(사실): 코픽스 신규취급액 3.18%(2026-08-18)" in page2 and "value=1.50" in page2


def test_calc_page_default_stress_is_regulated_3pp():
    page = ms.render_calc_page(None, "2026-09-07")
    assert 'id=stress type=number step=0.1 value=3.00' in page and "10.15 대책" in page and "max=30" in page
    rows = ms.build_dsr_table(100_000_000, 4.5, 30, ms.DEFAULT_STRESS_PP, cap_krw=600_000_000)
    assert rows[2]["stress_rate_pct"] == 7.5 and rows[2]["limit_krw"] == 476_725_424       # 소득 1억·4.5%+3.0·30년(Wolfram)
    assert ms.build_dsr_table(100_000_000, 4.5, 40, 3.0)[2]["limit_krw"] == 476_725_424       # 만기 40 입력도 30년 상한
    assert ms.build_dsr_table(100_000_000, 0.0, 30, 0.0)[0]["limit_krw"] == int(100_000_000 * 0.4 / 12 * 360)   # 0금리(음수 행 제외 → 0bp 가 첫 행, S6 Codex F10)


def test_calc_page_description_and_notice_pass_guards():
    import re
    from blog.wording_guard import assert_lead_wording_ok, assert_wording_ok
    page = ms.render_calc_page(None, "2026-09-07")
    desc = re.search(r'<meta name=description content="([^"]*)"', page).group(1)
    assert_wording_ok(desc, "calc:description"); assert_lead_wording_ok(desc, "calc:description")
    assert "직접 확인" in page and "실거래 통계가 아니다" in page and "[가정]" in page and "id=d-rate" in page
    assert "코드 기본값" in page or "PolicyParams 캐시" in page


def test_calc_js_mirrors_python_when_node_available():
    import shutil, subprocess
    if not shutil.which("node"):
        import pytest
        pytest.skip("node 없음")
    fns = "\n".join(l for l in ms._CALC_JS.split("\n") if l.startswith("function pmt") or l.startswith("function pv"))
    js = fns + "\nconsole.log(Math.floor(pmt(3e8,4.5,360)), Math.floor(pmt(3e8,4.75,360))-Math.floor(pmt(3e8,4.5,360)), Math.floor(pv(1e8*0.4/12,7.5,360)), Math.floor(pv(1e8*0.4/12,0,360)));"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout.split()
    assert [int(x) for x in out] == [1_520_055, 44_887, 476_725_424, int(1e8 * 0.4 / 12 * 360)]
    grid = re.search(r"var grid=function\(v\)\{[^}]*\};", ms._CALC_JS).group(0)                      # 페이지의 격자 가드 그대로
    probe = [7.005, 4.625, 1.005, 4.751, 4.5, 0.29, 1.15, 7.01, 3.0, 100.0]
    out = subprocess.run(["node", "-e", grid + f"\nconsole.log(JSON.stringify({probe}.map(grid)));"], capture_output=True, text=True, check=True).stdout.strip()
    assert json.loads(out) == [round(v, 2) == v for v in probe] == [False, False, False, False, True, True, True, True, True, True]   # JS 격자 = 파이썬 격자(S6c Codex R3)


def test_calc_js_guards_infinite_inputs_and_stress_note_separates_policy_default():
    page = ms.render_calc_page(None, "2026-09-07")
    assert "isFinite(P)&&isFinite(inc)&&isFinite(cap)" in page                     # loan=1e308 → Infinity 차단(S4 Codex)
    assert "rate>=0&&rate<=100&&st>=0&&st<=100&&fx>=0&&fx<=100" in page and "if(r<1e-9)return P/n" in page and "if(r<1e-9)return m*n" in page   # 1e308·1e-14 금리(S5 Codex)
    assert "(입력값) · 정책 기본값 3.00%p = 수도권·규제지역" in page and "입력을 바꾸면 위 값은 [가정]" in page
    assert "정책 기본값 3.00%p" in ms.render_calc_page(None, "2026-09-07", stress_pp=1.5)      # 초기 입력 1.5 여도 정책 기본값은 3.00(S5 Codex)
    assert ms.compute_monthly_payment(300_000_000, 1e-14, 30) == 300_000_000 // 360      # 미소 금리 = 0금리 산식(ZeroDivision 없음)
    assert ms.build_dsr_table(100_000_000, 1e-14, 30, 0, steps_bp=(0,), cap_krw=600_000_000)[0]["limit_krw"] == 600_000_000   # 파이썬 DSR 도 같은 문턱(S6 Codex F9)
    assert [r["step_bp"] for r in ms.build_scenario_table(300_000_000, 30, 0.0)] == [0, 25, 50]                 # 음수 금리 행 없음(F10)
    assert [r["step_bp"] for r in ms.build_dsr_table(100_000_000, 0.25, 30, 3.0)] == [-25, 0, 25, 50]
    assert "if(r<0)return;" in page and page.count("if(r<0)return;") == 2 and "P<=1e13&&" in page and "inc<=1e13" in page   # JS 동일 규칙 + 금액 상한(F8)
    assert f"max={ms.MAX_INPUT_KRW // 10**8}" in page and f"max={ms.MAX_INPUT_KRW // 10**4}" in page
    assert "return v===''?NaN:+v" in page and "id=calc-msg" in page and "t('calc-msg','입력이 비었거나 범위 밖" in page   # 빈칸 = 0 아님(S6b Codex N3)
    assert "var gap=Math.round((fx-rate)*100)/100;" in page and "grid(rate)&&grid(st)&&grid(fx)" in page and "소수 둘째 자리까지" in page   # 격자 밖은 양쪽 다 거부(N4 → S6c R3)
    for bad in ((4.751, 4.50), (7.005, 4.50), (4.625, 4.50), (4.50, 1.005)):
        with pytest.raises(ValueError):
            ms.compute_breakeven(*bad)
    assert ms.compute_breakeven(7.01, 4.50) == {"gap_pp": 2.51, "steps_25bp": 11} and ms.compute_breakeven(0.29, 0.0) == {"gap_pp": 0.29, "steps_25bp": 2}
