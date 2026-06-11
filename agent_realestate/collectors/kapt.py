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
BASIS_EP = "http://apis.data.go.kr/1613000/AptBasisInfoServiceV3/getAphusBassInfoV3"


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
    it = root.find(".//item") or root
    def t(tag):
        return (it.findtext(tag) or "").strip()
    used = t("kaptUsedate")          # YYYYMMDD
    return {
        "kaptName": t("kaptName"),
        "units": int(t("kaptdaCnt") or 0),          # 세대수
        "dong_cnt": int(t("kaptDongCnt") or 0),     # 동수
        "built_year": int(used[:4]) if len(used) >= 4 and used[:4].isdigit() else 0,
    }


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
    meta = parse_basis(_get(BASIS_EP, {"kaptCode": hit["kaptCode"]}, key))
    meta["kaptCode"] = hit["kaptCode"]
    return meta
