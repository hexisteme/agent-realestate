"""R5 K-apt(공동주택 기본정보) 파서 — 네트워크 없이 샘플 XML."""
from agent_realestate.collectors.kapt import parse_apt_list, parse_basis, parse_maint_fee

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


# ── parse_maint_fee 단위테스트 (네트워크 없음) ────────────────────────────────
_GUARD_OK = """<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><item>
    <kaptCode>A13558509</kaptCode><kaptName>상계주공3단지</kaptName>
    <guardCost>15000000</guardCost>
  </item></body>
</response>"""

_CLEAN_OK = """<response>
  <header><resultCode>00</resultCode></header>
  <body><item>
    <kaptCode>A13558509</kaptCode><kaptName>상계주공3단지</kaptName>
    <cleanCost>8500000</cleanCost>
  </item></body>
</response>"""

_OFFICE_OK = """<response>
  <header><resultCode>00</resultCode></header>
  <body><item>
    <kaptCode>A13558509</kaptCode><kaptName>상계주공3단지</kaptName>
    <officeSupply>300000</officeSupply><bookSupply>100000</bookSupply><transportCost>50000</transportCost>
  </item></body>
</response>"""

_ERROR_CODE = """<response>
  <header><resultCode>03</resultCode><resultMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</resultMsg></header>
  <body></body>
</response>"""

_NO_ITEM = """<response>
  <header><resultCode>00</resultCode></header>
  <body></body>
</response>"""


def test_parse_maint_fee_single_field():
    assert parse_maint_fee(_GUARD_OK, ["guardCost"]) == 15_000_000


def test_parse_maint_fee_single_field_clean():
    assert parse_maint_fee(_CLEAN_OK, ["cleanCost"]) == 8_500_000


def test_parse_maint_fee_multi_field():
    assert parse_maint_fee(_OFFICE_OK, ["officeSupply", "bookSupply", "transportCost"]) == 450_000


def test_parse_maint_fee_error_code_returns_zero():
    assert parse_maint_fee(_ERROR_CODE, ["guardCost"]) == 0


def test_parse_maint_fee_no_item_returns_zero():
    assert parse_maint_fee(_NO_ITEM, ["guardCost"]) == 0


def test_parse_maint_fee_wrong_field_returns_zero():
    # guardCost exists but we ask for cleanCost
    assert parse_maint_fee(_GUARD_OK, ["cleanCost"]) == 0


def test_parse_maint_fee_bad_xml_returns_zero():
    assert parse_maint_fee("not xml at all", ["guardCost"]) == 0
