"""실거래 추세 (②)."""
from agent_realestate.analysts.trend import compute_trend


def test_trend_none_under_two():
    assert compute_trend([]) is None
    assert compute_trend([{"deal_ym": "2026-01", "price_krw": 800_000_000}]) is None


def test_trend_change_pct():
    t = compute_trend([{"deal_ym": "2026-01", "price_krw": 800_000_000},
                       {"deal_ym": "2026-05", "price_krw": 830_000_000}])
    assert t.n == 2
    assert t.last_ym == "2026-05"
    assert abs(t.change_pct_total - 3.8) < 0.2     # +3.75%


def test_trend_sorts_unordered():
    t = compute_trend([{"deal_ym": "2026-05", "price_krw": 830_000_000},
                       {"deal_ym": "2026-01", "price_krw": 800_000_000}])
    assert t.first_ym == "2026-01" and t.last_ym == "2026-05"
