"""R5 K-apt(공동주택 기본정보) 파서 — 네트워크 없이 샘플 XML."""
from agent_realestate.collectors.kapt import parse_apt_list, parse_basis

LIST_XML = """<response><body><items>
<item><kaptCode>A13558509</kaptCode><kaptName>상계주공3단지</kaptName></item>
<item><kaptCode>A13558510</kaptCode><kaptName>상계주공5단지</kaptName></item>
</items></body></response>"""

BASIS_XML = """<response><body><item>
<kaptName>상계주공3단지</kaptName><kaptdaCnt>2213</kaptdaCnt>
<kaptDongCnt>26</kaptDongCnt><kaptUsedate>19871130</kaptUsedate>
</item></body></response>"""


def test_parse_apt_list():
    lst = parse_apt_list(LIST_XML)
    assert len(lst) == 2
    assert lst[0]["kaptCode"] == "A13558509" and lst[0]["kaptName"] == "상계주공3단지"


def test_parse_basis():
    m = parse_basis(BASIS_XML)
    assert m["units"] == 2213
    assert m["dong_cnt"] == 26
    assert m["built_year"] == 1987
