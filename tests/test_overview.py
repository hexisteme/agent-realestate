"""네이버 overview 파서 테스트 (현재 호가 + 실거래 괴리)."""
from agent_realestate.collectors.naver_overview import parse_overview

SAMPLE = '''[
 {"query":"등촌주공5단지","found":true,"complexNo":"13","complexName":"등촌주공5단지","type":"아파트",
  "households":1045,"builtYmd":"19951201","askingMinManwon":81000,"askingMaxManwon":125000,
  "askingMin":"8억 1,000","askingMax":"12억 5,000","recentDealManwon":94300,"recentDeal":"9억 4,300",
  "recentDealYmd":"2026.05.07","recentDealAreaM2":58.0,"lat":37.55,"lng":126.85},
 {"query":"없는단지","found":false}
]'''

def test_parse_basic():
    rows = parse_overview(SAMPLE)
    assert len(rows) == 2
    o = rows[0]
    assert o.found and o.complex_no == "13" and o.households == 1045 and o.built_year == 1995
    assert o.asking_min_manwon == 81000 and o.recent_deal_manwon == 94300
    assert rows[1].found is False

def test_gap_pct():
    o = parse_overview(SAMPLE)[0]
    # 호가하한 81000 ↔ 실거래 94300 → 호가가 실거래보다 -14% (저평가 신호)
    assert o.gap_pct is not None and -16 < o.gap_pct < -12

def test_gap_none_when_missing():
    rows = parse_overview('[{"query":"x","found":true,"complexName":"X","askingMinManwon":50000}]')
    assert rows[0].gap_pct is None
