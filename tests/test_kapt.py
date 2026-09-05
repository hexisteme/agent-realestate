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


def test_basis_endpoints_are_v5_live_successor():
    """2026-09-05: V3/V4/AptListService3 는 HTTP 400 code 12(폐기). 후계 V5 는 data.go.kr 15058453 swagger 와 일치해야 한다."""
    from agent_realestate.collectors import kapt
    assert kapt.BASIS_EP_V5 == "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5"
    assert kapt.DETAIL_EP_V5 == "https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusDtlInfoV5"
    assert not hasattr(kapt, "BASIS_EP_V4")           # 폐기 엔드포인트로 되돌아가는 회귀 차단
    assert kapt.fetch_basis_v4 is kapt.fetch_basis     # 구 이름 호환


def test_fetch_basis_merges_v5_basis_and_detail(monkeypatch):
    from agent_realestate.collectors import kapt
    calls = []
    def fake(url, params, key):
        calls.append(url)
        if url == kapt.BASIS_EP_V5:
            return {"kaptName": "영등포푸르지오", "kaptdaCnt": 2462.0, "hoCnt": 2470.0, "kaptDongCnt": 20.0, "kaptUsedate": "20020930",
                    "kaptAddr": "서울특별시 영등포구 영등포동 1-1 영등포푸르지오",
                    "codeHeatNm": "개별난방", "codeHallNm": "계단식", "kaptBcompany": "대우건설"}
        assert url == kapt.DETAIL_EP_V5
        return {"kaptdPcnt": 1000.0, "kaptdPcntu": 1462.0}
    monkeypatch.setattr(kapt, "_get_json_item", fake)
    m = kapt.fetch_basis("A15003002", key="k")
    assert calls == [kapt.BASIS_EP_V5, kapt.DETAIL_EP_V5]
    assert (m["kaptName"], m["units"], m["dong_cnt"], m["built_year"]) == ("영등포푸르지오", 2462, 20, 2002)
    assert m["builder"] == "대우건설" and m["parking_total"] == 2462 and m["parking_per_unit"] == 1.0
    # 신원게이트(verify_kapt_basis_identity)용 원시값 — 주소·세대수(kaptdaCnt)·호수(hoCnt, 주상복합 폴백) 노출(2026-09-05)
    assert (m["kaptAddr"], m["kaptdaCnt"], m["hoCnt"]) == ("서울특별시 영등포구 영등포동 1-1 영등포푸르지오", 2462, 2470)


def test_list_endpoint_is_v4_and_parse_apt_list_accepts_v4_json():
    """2026-09-05: AptListService3 폐기 → V4(JSON). XML(V3 픽스처) 파싱은 그대로 유지."""
    from agent_realestate.collectors import kapt
    assert kapt.LIST_EP == "https://apis.data.go.kr/1613000/AptListService4/getSigunguAptList4"
    body = ('{"response":{"body":{"items":[{"kaptCode":"A10020255","kaptName":"월계건양 노블레스 아파트 ","bjdCode":"1135010200"},'
            '{"kaptCode":"","kaptName":"무코드"}],"numOfRows":3,"pageNo":1,"totalCount":2},"header":{"resultCode":"00"}}}')
    assert kapt.parse_apt_list(body) == [{"kaptCode": "A10020255", "kaptName": "월계건양 노블레스 아파트"}]
    assert kapt.parse_apt_list("{not json") == []
