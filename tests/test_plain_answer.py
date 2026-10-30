"""한눈에보기(PlainAnswer) — 일반 독자용 3줄(가격·거래·대출 금리). DIKW 검토 2026-10-30 P0."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest

from blog import market_charts as mch
from blog.wording_guard import assert_wording_ok
from test_market_charts import snapshot

_COUNTS = {"n_total": 1751, "n_judged": 494, "up": 476, "down": 18}
_JARGON = ("중위", "52주", "게이트", "유효짝", "IQR", "3·9개월", "판정")
_JUDGEMENT = ("적기", "추천", "전망", "오를", "내릴", "기회", "매수", "매도", "바닥", "고점", "사도")


def test_plain_answer_has_three_plain_lines_in_fixed_order():
    d = mch.build_market_dashboard(snapshot(), "2026-10-10")
    lines = mch.plain_answer_lines(_COUNTS, d)
    assert [line.split(" — ")[0] for line in lines] == ["가격", "거래", "대출 금리"]
    assert "거래가 충분한 단지 494곳 중 476곳(96%)에서 최근 3개월 가격이 그 전 9개월보다 높았습니다" in lines[0]
    assert "8월 서울 아파트 매매 신고는 6,300호로 7월보다 1.6% 많았습니다" in lines[1]
    assert "8월 새로 나간 주택담보대출 평균 금리는 4.43%로 1년 전보다 0.36%p 높습니다" in lines[2]
    text = " ".join(lines)
    for word in _JARGON + _JUDGEMENT:
        assert word not in text, word
    assert_wording_ok(text, "plain_answer")


def test_plain_answer_states_fall_and_flat_in_words():
    d = mch.build_market_dashboard(snapshot(), "2026-10-10")
    falling = {"n_total": 100, "n_judged": 40, "up": 10, "down": 30}
    assert "40곳 중 30곳(75%)에서 최근 3개월 가격이 그 전 9개월보다 낮았습니다" in mch.plain_answer_lines(falling, d)[0]
    import dataclasses
    flat_trades = dataclasses.replace(d.trades, points=(("2026-07", 6300.0), ("2026-08", 6300.0)), mom=0.0)
    line = mch.plain_answer_lines(_COUNTS, dataclasses.replace(d, trades=flat_trades))[1]
    assert "7월과 같았습니다" in line


def test_plain_answer_drops_lines_without_data_and_never_invents():
    assert mch.plain_answer_lines({}, None) == []
    only_price = mch.plain_answer_lines(_COUNTS, None)
    assert len(only_price) == 1 and only_price[0].startswith("가격 — ")
    assert mch.plain_answer_lines({"n_total": 9, "n_judged": 0, "up": 0, "down": 0}, None) == []


def test_visual_first_puts_plain_answer_before_the_technical_summary():
    from blog import visual_first as vf
    rows = [dict(gu="강남", name=f"단지{i}", product_type="아파트", area_m2=59.0, molit_n=15, molit_recent_eok=10.0,
                 molit_trend_dir="▲" if i < 3 else "▼", molit_trend_pct=3.0, molit_trend_n_recent=3,
                 molit_trend_n_prior=6) for i in range(4)]
    d = mch.build_market_dashboard(snapshot(), "2026-10-10")
    head = vf._plain_html({"n_total": 4}, d, {"complexes": rows})
    assert head.startswith("• 가격 — ") and "<b>" not in head.split("<br><br>")[0]
    assert head.endswith("<b>자세한 집계</b><br>")
    gated = vf.count_gated_trends(rows)
    assert f"{gated['n_judged']}곳 중" in head
    assert vf._plain_html({}, None, {"complexes": []}) == ""


def test_codex_r2_tiny_trade_change_is_not_called_the_same():
    # Codex R2(2026-10-30): 6,300→6,301호는 mom 0.0% 로 반올림되지만 '같았습니다'는 거짓
    import dataclasses
    d = mch.build_market_dashboard(snapshot(), "2026-10-10")
    tr = dataclasses.replace(d.trades, points=(("2026-07", 6300.0), ("2026-08", 6301.0)), mom=0.0)
    line = mch.plain_answer_lines(_COUNTS, dataclasses.replace(d, trades=tr))[1]
    assert "같았습니다" not in line and "7월보다 1호 많았습니다" in line
    same = dataclasses.replace(d.trades, points=(("2026-07", 6300.0), ("2026-08", 6300.0)), mom=0.0)
    assert "7월과 같았습니다" in mch.plain_answer_lines(_COUNTS, dataclasses.replace(d, trades=same))[1]


@pytest.mark.parametrize("counts,expect", [
    ({"n_judged": 100, "up": 0, "down": 0}, "100곳 모두 최근 3개월 가격이 그 전 9개월과 같았습니다"),
    ({"n_judged": 10, "up": 4, "down": 4}, "10곳 중 높아진 곳과 낮아진 곳이 4곳씩 같았습니다(나머지 2곳은 변화 없음)"),
    ({"n_judged": 10, "up": 6, "down": 1}, "10곳 중 6곳(60%)에서 최근 3개월 가격이 그 전 9개월보다 높았습니다(낮아진 곳 1곳, 변화 없는 곳 3곳)"),
])
def test_codex_r2_flat_and_tied_price_counts_are_stated_as_they_are(counts, expect):
    line = mch.plain_answer_lines(counts, None)[0]
    assert expect in line, line
