"""R5/R6/R8/R9 보강 테스트."""
import agent_realestate.config as cfg
from agent_realestate.analysts.finance import compute_capital_gains_tax, compute_dsr_loan
from agent_realestate.analysts.location import parse_location
from agent_realestate.cache import store


# R9 입지 정량화
def test_r9_parse_location():
    lp = parse_location("노원역 7호선·4호선·GTX-C 창동 도보 5분")
    assert lp.line_count == 3                  # 7호선·4호선·GTX-C
    assert lp.walk_min == 5
    assert lp.has_gtx is True
    assert lp.score >= 4.5                     # 3노선+도보5+GTX
def test_r9_empty():
    lp = parse_location("")
    assert lp.line_count == 0 and lp.score == 2.0
def test_r9_school():
    lp = parse_location("하계역 7호선 은행사거리 학군")
    assert lp.school_signal is True


# R6 DSR 기존부채 반영
def test_r6_existing_debt_reduces_loan():
    no_debt = compute_dsr_loan(100_000_000, 0.043, 40, 0.40, existing_annual_debt_krw=0)
    with_debt = compute_dsr_loan(100_000_000, 0.043, 40, 0.40, existing_annual_debt_krw=15_000_000)
    assert with_debt < no_debt                 # 기존부채 상환액만큼 한도↓


# R8 양도소득세
def test_r8_one_home_under_12_exempt():
    assert compute_capital_gains_tax(800_000_000, 1_000_000_000, 3, is_one_home=True, resident_years=3) == 0
def test_r8_over_12_taxed():
    assert compute_capital_gains_tax(1_000_000_000, 1_500_000_000, 3, is_one_home=True, resident_years=3) > 0
def test_r8_short_hold_heavier():
    short = compute_capital_gains_tax(1_000_000_000, 1_500_000_000, 0.5, is_one_home=False)
    long = compute_capital_gains_tax(1_000_000_000, 1_500_000_000, 5, is_one_home=False)
    assert short > long                        # 단기 중과 70% > 누진


# R5 단지 메타 캐시 (부분일치)
def test_r5_meta_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(store, "CACHE_DB", tmp_path / "t.sqlite")
    conn = store.connect()
    store.upsert_meta(conn, "상계주공3단지", 2213, 180, 1987, "ddm", "2026-05-28")
    m = store.get_meta(conn, "상계주공3")        # 후보명(단지 없는)으로 조회 → 부분일치
    assert m is not None and m["units"] == 2213 and m["built_year"] == 1987
