"""naver_region 파서 테스트 — 마커 API JSON → RegionComplex, 필터/dedup 은 cli 책임."""
from agent_realestate.collectors.naver_region import DEFAULT_SCRIPT, SEOUL_GU, parse_region


SAMPLE = """[
  {"complexNo":"199","name":"상계주공14단지","lat":37.66,"lng":127.07,"far":147,
   "builtYm":"198904","households":2265,"dongs":24,"minArea":"49.5","maxArea":"79.3","dealCount":0,"leaseCount":"7","rentCount":4,"shortTermRentCount":"1"},
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
    assert a.deal_count == 0 and a.lease_count == 7
    assert a.rent_count == 4 and a.short_term_rent_count == 1
    assert rows[1].deal_count is None and rows[1].lease_count is None


def test_parse_region_far_missing_is_zero():
    rows = parse_region('[{"complexNo":"1","name":"미륭","far":0,"builtYm":"198606","households":3930}]')
    assert rows[0].far_pct == 0          # 0 = 용적률 미기재 (cli 에서 '미기재' 표기)


def test_parse_region_error_dict_returns_empty():
    assert parse_region('{"error":"no regionList"}') == []


def test_parse_region_rejects_invalid_or_negative_article_counts():
    invalid = '[{"complexNo":"1","name":"x","far":1,"builtYm":"200001","households":1,"dealCount":-1}]'
    try:
        parse_region(invalid)
    except ValueError as exc:
        assert "dealCount" in str(exc)
    else:
        raise AssertionError("negative dealCount must fail")

    fraction = '[{"complexNo":"1","name":"x","far":1,"builtYm":"200001","households":1,"leaseCount":"1.5"}]'
    try:
        parse_region(fraction)
    except ValueError as exc:
        assert "leaseCount" in str(exc)
    else:
        raise AssertionError("fractional leaseCount must fail")


def test_seoul_gu_map_has_all_25():
    assert len(SEOUL_GU) == 25
    assert SEOUL_GU["노원"] == "1135000000"


def test_default_script_points_to_codex_runtime():
    assert "/.codex/scripts/naver-region-scan.sh" in DEFAULT_SCRIPT
    assert "/.claude/" not in DEFAULT_SCRIPT
