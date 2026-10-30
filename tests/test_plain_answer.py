"""한눈에보기(PlainAnswer) — 일반 독자용 3줄(가격·거래·대출 금리). DIKW 검토 2026-10-30 P0."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

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
    flat_trades = dataclasses.replace(d.trades, mom=0.0)
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
