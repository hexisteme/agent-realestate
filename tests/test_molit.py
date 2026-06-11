"""MOLIT XML 파서 (② 자동수집). 네트워크 없이 샘플 XML 로 검증."""
from agent_realestate.collectors.molit import filter_by_complex, parse_items

SAMPLE = """<response><body><items>
<item><aptNm>상계주공3단지</aptNm><excluUseAr>58.46</excluUseAr><dealAmount>83,000</dealAmount>
<dealYear>2026</dealYear><dealMonth>5</dealMonth><dealDay>10</dealDay><floor>5</floor><umdNm>상계동</umdNm></item>
<item><aptNm>다른단지</aptNm><excluUseAr>84.9</excluUseAr><dealAmount>120,000</dealAmount>
<dealYear>2026</dealYear><dealMonth>4</dealMonth><dealDay>2</dealDay><floor>10</floor><umdNm>상계동</umdNm></item>
</items></body></response>"""


def test_parse_items():
    items = parse_items(SAMPLE)
    assert len(items) == 2
    s3 = items[0]
    assert s3["complex_name"] == "상계주공3단지"
    assert s3["price_krw"] == 830_000_000        # 83,000만원 → 8.3억
    assert s3["deal_ym"] == "2026-05"            # 월 zero-pad
    assert abs(s3["area_exclusive_m2"] - 58.46) < 0.01


def test_filter_by_complex():
    items = parse_items(SAMPLE)
    picked = filter_by_complex(items, "상계주공3")   # 부분일치(공백 무시)
    assert len(picked) == 1 and picked[0]["complex_name"] == "상계주공3단지"


def test_parse_skips_empty_amount():
    xml = "<response><items><item><aptNm>X</aptNm><dealAmount></dealAmount></item></items></response>"
    assert parse_items(xml) == []
