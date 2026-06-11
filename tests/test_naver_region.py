"""naver_region 파서 테스트 — 마커 API JSON → RegionComplex, 필터/dedup 은 cli 책임."""
from agent_realestate.collectors.naver_region import parse_region, SEOUL_GU


SAMPLE = """[
  {"complexNo":"199","name":"상계주공14단지","lat":37.66,"lng":127.07,"far":147,
   "builtYm":"198904","households":2265,"dongs":24,"minArea":"49.5","maxArea":"79.3"},
  {"complexNo":"326","name":"창동주공3단지","far":175,"builtYm":"199009","households":2856}
]"""


def test_parse_region_basic():
    rows = parse_region(SAMPLE, district="노원")
    assert len(rows) == 2
    a = rows[0]
    assert a.complex_no == "199" and a.name == "상계주공14단지"
    assert a.far_pct == 147 and a.built_year == 1989 and a.households == 2265
    assert a.district == "노원"
    assert a.min_area_m2 == 49.5


def test_parse_region_far_missing_is_zero():
    rows = parse_region('[{"complexNo":"1","name":"미륭","far":0,"builtYm":"198606","households":3930}]')
    assert rows[0].far_pct == 0          # 0 = 용적률 미기재 (cli 에서 '미기재' 표기)


def test_parse_region_error_dict_returns_empty():
    assert parse_region('{"error":"no regionList"}') == []


def test_seoul_gu_map_has_all_25():
    assert len(SEOUL_GU) == 25
    assert SEOUL_GU["노원"] == "1135000000"
