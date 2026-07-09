"""R5 단지 메타 자동수집 — 국토교통부 공동주택 기본정보(K-apt) OpenAPI (무료, data.go.kr).
data.go.kr ID 15058453. **MOLIT_API_KEY(데이터포털 디코딩키) 재사용** — 해당 API 활용신청만 추가
(무료·자동승인). 2단계: 시군구 단지목록(kaptCode) → 기본정보(세대수·준공·동수).

stdlib 만. 라이브 검증은 활용신청 후 수행(엔드포인트/필드는 공식 문서 기준, 미검증 표시).
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

LIST_EP = "http://apis.data.go.kr/1613000/AptListService3/getSigunguAptList3"
# V3 폐기 확인(2026-07-07 라이브: 'Unexpected errors') — V4 는 JSON 응답, 필드명 변경
# (kaptHeating→codeHeatNm, kaptMangeTrunk→codeHallNm, kaptBuild→kaptBcompany,
#  주차는 상세(getAphusDtlInfoV4)의 kaptdPcnt(지상)+kaptdPcntu(지하)).
BASIS_EP = "http://apis.data.go.kr/1613000/AptBasisInfoServiceV3/getAphusBassInfoV3"  # (폐기·테스트 픽스처용)
BASIS_EP_V4 = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusBassInfoV4"
DETAIL_EP_V4 = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusDtlInfoV4"

# ── 공용관리비 API (서비스 ID 15057937) ──────────────────────────────────────
# 현행 = V2(1613000, JSON) — 구 1611000 XML 서비스는 폐기(전 버전 HTTP500, 2026-07-08 실측).
# 파라미터: serviceKey, kaptCode, searchDate(YYYYMM)  |  응답: JSON (필드명은 구판과 동일)
# 값 단위: 단지 총액(원/월) — 세대당 환산은 호출자가 ÷ kaptdaCnt
# ※ 별도 활용신청 필요 — data.go.kr 서비스 ID 15057937 (2026-07-08 승인·7개 오퍼레이션 라이브 검증)
# ※ 미포함(엔드포인트 미확정): 인건비·교육훈련비·소독비·시설유지비·안전점검비·
#    제세공과금·차량유지비·피복비·기타부대비용·지능형홈네트워크
MAINT_BASE = "https://apis.data.go.kr/1613000/AptCmnuseManageCostServiceV2"

_MAINT_SUBS: list[tuple[str, list[str]]] = [
    ("getHsmpGuardCostInfoV2",              ["guardCost"]),                                    # 경비비
    ("getHsmpCleaningCostInfoV2",           ["cleanCost"]),                                    # 청소비
    ("getHsmpRepairsCostInfoV2",            ["lrefCost1"]),                                    # 수선비
    ("getHsmpElevatorMntncCostInfoV2",      ["elevCost"]),                                     # 승강기유지비
    ("getHsmpConsignManageFeeInfoV2",       ["manageCost"]),                                   # 위탁관리수수료
    ("getHsmpDisasterPreventionCostInfoV2", ["lrefCost4"]),                                    # 재해예방비
    ("getHsmpOfcrkCostInfoV2",              ["officeSupply", "bookSupply", "transportCost"]),  # 제사무비
]


def _get(url: str, params: dict, key: str) -> str:
    qs = urllib.parse.urlencode({**params, "serviceKey": key})
    try:
        with urllib.request.urlopen(f"{url}?{qs}", timeout=20) as r:
            return r.read().decode("utf-8")
    except Exception as e:
        raise SystemExit(f"K-apt 요청 실패: {str(e).replace(key, '***KEY***')}")


def parse_apt_list(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for it in root.iter("item"):
        code = it.findtext("kaptCode") or ""
        name = it.findtext("kaptName") or ""
        if code:
            out.append({"kaptCode": code.strip(), "kaptName": name.strip()})
    return out


def parse_basis(xml_text: str) -> dict:
    root = ET.fromstring(xml_text)
    it = root.find(".//item")
    if it is None:
        it = root
    def t(tag):
        return (it.findtext(tag) or "").strip()
    used = t("kaptUsedate")          # YYYYMMDD
    park = t("kaptParking")                              # 총주차대수(kaptParking)
    parking_total = int(park) if park.isdigit() else None
    units = int(t("kaptdaCnt") or 0)                     # 세대수
    return {
        "kaptName": t("kaptName"),
        "units": units,
        "dong_cnt": int(t("kaptDongCnt") or 0),     # 동수
        "built_year": int(used[:4]) if len(used) >= 4 and used[:4].isdigit() else 0,
        "heating": t("kaptHeating") or None,             # 난방방식(개별/지역/중앙난방)
        "corridor_type": t("kaptMangeTrunk") or None,    # 복도유형(계단식/복도식/혼합)
        "parking_total": parking_total,                  # 총주차대수
        "builder": t("kaptBuild") or None,               # 시공사명
        "parking_per_unit": round(parking_total / units, 2) if parking_total and units else None,  # 세대당 주차대수(파생)
    }


def _get_json_item(url: str, params: dict, key: str) -> dict:
    """V4 계열(JSON 응답) 단건 item 추출. 실패/형식이상은 빈 dict — 배치 지속성 우선."""
    import json as _j
    qs = urllib.parse.urlencode({**params, "serviceKey": key})
    try:
        with urllib.request.urlopen(f"{url}?{qs}", timeout=20) as r:
            body = _j.loads(r.read().decode("utf-8"))
        return body.get("response", {}).get("body", {}).get("item", {}) or {}
    except Exception:
        return {}


def fetch_basis_v4(kapt_code: str, key: str | None = None) -> dict | None:
    """K-apt 기본정보 V4 — basis(난방·복도·시공사·세대수·준공) + detail(주차) 2콜 병합.
    parse_basis(V3 XML)와 동일 키 + heating/corridor_type/builder/parking_per_unit."""
    key = key or os.environ.get("MOLIT_API_KEY", "")
    if not key:
        return None
    b = _get_json_item(BASIS_EP_V4, {"kaptCode": kapt_code}, key)
    if not b:
        return None
    d = _get_json_item(DETAIL_EP_V4, {"kaptCode": kapt_code}, key)

    def _i(v) -> int:
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0

    units = _i(b.get("kaptdaCnt"))
    used = str(b.get("kaptUsedate") or "")
    parking_total = (_i(d.get("kaptdPcnt")) + _i(d.get("kaptdPcntu"))) or None
    return {
        "kaptName": (b.get("kaptName") or "").strip(),
        "units": units,
        "dong_cnt": _i(b.get("kaptDongCnt")),
        "built_year": int(used[:4]) if used[:4].isdigit() else 0,
        "heating": (b.get("codeHeatNm") or "").strip() or None,
        "corridor_type": (b.get("codeHallNm") or "").strip() or None,
        "builder": (b.get("kaptBcompany") or "").strip() or None,
        "parking_total": parking_total,
        "parking_per_unit": round(parking_total / units, 2) if parking_total and units else None,
    }


def parse_maint_fee(xml_text: str, fee_fields: list[str]) -> int:
    """단일 K-apt 공용관리비 엔드포인트 XML 응답에서 지정 필드 합산(원).

    resultCode 가 오류이거나 <item> 이 없으면 0 반환.
    값은 단지 총액(원/월) — 세대당 환산은 호출자 책임.
    """
    try:
        root = ET.fromstring(xml_text)
        rc = (root.findtext(".//resultCode") or "").strip()
        if rc and rc not in ("00", "0000"):
            return 0
        item = root.find(".//item")
        if item is None:
            return 0
        total = 0
        for field in fee_fields:
            raw = (item.findtext(field) or "0").strip()
            if raw.lstrip("-").isdigit():
                total += max(0, int(raw))
        return total
    except ET.ParseError:
        return 0


def fetch_maint_fee(kapt_code: str, search_date: str, key: str | None = None) -> int | None:
    """K-apt 공용관리비 API(서비스 ID 15057937)로 단지 총 공용관리비(원/월) 반환.

    params:
        kapt_code:   K-apt 단지코드 (예: "A13558509")
        search_date: 조회월 YYYYMM (예: "202301")
        key:         data.go.kr 서비스키 (None → MOLIT_API_KEY 환경변수)
    returns:
        int  — 단지 총 공용관리비(원/월). 세대당: 반환값 ÷ kaptdaCnt.
        None — 키 미승인 / HTTP 500 / 해당 월 데이터 없음.

    포함 항목: 경비비·청소비·수선비·승강기유지비·위탁관리수수료·재해예방비·제사무비.
    미포함(인건비 등): 엔드포인트 미확정 항목은 MAINT_BASE 주석 참조.
    """
    key = key or os.environ.get("MOLIT_API_KEY", "")
    if not key:
        return None
    total = 0
    found_any = False
    for ep, fields in _MAINT_SUBS:
        # V2 는 JSON — item 에서 필드 합산 (음수/비수치는 0 취급)
        item = _get_json_item(MAINT_BASE + "/" + ep,
                              {"kaptCode": kapt_code, "searchDate": search_date}, key)
        amt = 0
        for f in fields:
            v = item.get(f)
            try:
                amt += max(0, int(float(v)))
            except (TypeError, ValueError):
                continue
        if amt > 0:
            found_any = True
            total += amt
    return total if found_any else None


def fetch_meta_for(district: str, complex_name: str, key: str | None = None) -> dict | None:
    """district(서울 자치구) + 단지명 → {units, built_year, dong_cnt}. 미매칭 None.
    용적률은 K-apt 기본정보에 없음 → 별도(사용자/추정)."""
    from .lawd import lawd_for_district
    key = key or os.environ.get("MOLIT_API_KEY", "")
    if not key:
        raise SystemExit("MOLIT_API_KEY 미설정 (K-apt 도 같은 data.go.kr 키 사용)")
    sgg = lawd_for_district(district)
    if not sgg:
        raise SystemExit(f"시군구코드 미해결: {district}")
    lst = parse_apt_list(_get(LIST_EP, {"sigunguCode": sgg, "numOfRows": 5000, "pageNo": 1}, key))
    norm = complex_name.replace(" ", "")
    hit = next((a for a in lst if norm in a["kaptName"].replace(" ", "")), None)
    if not hit:
        return None
    meta = fetch_basis_v4(hit["kaptCode"], key)   # V3 폐기 → V4 (2026-07-07)
    if not meta:
        return None
    meta["kaptCode"] = hit["kaptCode"]
    return meta
