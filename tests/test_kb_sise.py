"""KB시세 파서 테스트."""
from agent_realestate.collectors.kb_sise import parse_kb_sise

SAMPLE = '{"code":"466","name":"등촌5단지주공","mae_ilban_manwon":99000,"mae_upper_manwon":103000,"mae_lower_manwon":92500,"mae_listing_avg_manwon":112733,"jeonse_ilban_manwon":48000}'

def test_parse():
    s = parse_kb_sise(SAMPLE)
    assert s.code == "466" and s.mae_ilban_manwon == 99000
    assert s.mae_ilban_krw == 990_000_000

def test_listing_gap():
    s = parse_kb_sise(SAMPLE)
    # 매물평균 112733 ↔ KB일반가 99000 → +13.9%
    assert s.listing_vs_kb_pct is not None and 12 < s.listing_vs_kb_pct < 16

def test_missing_graceful():
    s = parse_kb_sise('{"code":"1","name":"X"}')
    assert s.mae_ilban_krw is None and s.listing_vs_kb_pct is None
