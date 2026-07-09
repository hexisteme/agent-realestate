"""공동주택 공시가격 조회 — 국토부 공동주택 공시가격 공개 API (data.go.kr). stdlib 만.

세금 산정 기준값(재산세·종부세 과표)을 실측 공시가로 채우기 위한 수집기.
분석 finance.py 는 현재 공시가를 매매가 × official_ratio_tiered [추론] 근사로 쓰는데,
이 값이 있으면 그 가정을 실측 [사실] 로 대체한다(OfficialPrice.taxBasis, glossary-real-estate).

엔드포인트/파라미터는 과제 스펙 기준 — **미검증 → 폐기 확인**(2026-07-07 라이브: ApartHousingPriceService3
은 V4 포함 전 버전 HTTP500 'Unexpected errors' = 서비스 부재). 현행 공시가 개방은
data.go.kr **15124003 '공동주택가격정보(WMS/WFS/속성정보)'** = VWorld NSDI 제공(별도 VWORLD_API_KEY).
→ **백필 완료(2026-07-09)**: repo 루트 `collect_gongsi.py`(gitignored dev tool)가 VWorld
`getApartHousingPriceAttr`(pnu 기반, pblntfPc 원 단위)로 universe gongsi_man 94/117 채움 —
pnu 는 kapt basis V4 bjdCode+지번 조립, 알리미(realtyprice.kr) 3표본 호 단위 대조 일치.
본 모듈의 ApartHousingPriceService3 경로는 사망 기록용으로만 유지.
  GET {GONGSI_EP}
  params: serviceKey, sidoCode, guCode(시군구코드), bjdCode(법정동코드), bldNm(건물명), dongCode
  반환(item): pblntfPc(공시가격, 만원), bldNm, dongCode
서비스키는 MOLIT_API_KEY 재사용(같은 data.go.kr 계정 — molit.py/kapt.py 와 동일). 키는 에러에서 마스킹.

대안(미채택): 한국부동산원 NSDI ApartHousingPriceService(1611000)/getApartHousingPriceAttr 는
pnu(19자리 필지고유번호)+stdrYear 를 받는다 — district+단지명+면적 시그니처엔 부적합하여 미채택.
"""

from __future__ import annotations

import os
import statistics
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .lawd import lawd_for_district

GONGSI_EP = "http://apis.data.go.kr/1613000/ApartHousingPriceService3/getApartHousingPriceInfo3"


def _get(url: str, params: dict, key: str) -> str:
    qs = urllib.parse.urlencode({**params, "serviceKey": key})
    try:
        with urllib.request.urlopen(f"{url}?{qs}", timeout=20) as r:
            return r.read().decode("utf-8")
    except Exception as e:
        raise SystemExit(f"공시가 요청 실패: {str(e).replace(key, '***KEY***')}")


def parse_gongsi(xml_text: str, complex_name: str) -> list[dict]:
    """공시가 XML → [{bldNm, gongsi_man}]. 단지명 부분일치로 필터(같은 법정동 다단지 혼입 방지)."""
    root = ET.fromstring(xml_text)
    norm = complex_name.replace(" ", "")
    out: list[dict] = []
    for it in root.iter("item"):
        name = (it.findtext("bldNm") or "").strip()
        pc = (it.findtext("pblntfPc") or "").replace(",", "").strip()
        if not pc.isdigit():
            continue
        if norm and norm not in name.replace(" ", ""):
            continue
        out.append({"bldNm": name, "gongsi_man": int(pc)})
    return out


def fetch_gongsi(district: str, complex_name: str, area_m2: float, key: str | None = None) -> int | None:
    """단지명+면적 → 공시가격(만원). 미매칭 None.

    현재 구현은 단지명 매칭 세대들의 공시가 중위를 반환(단지 대표값). area_m2 는 향후 동일면적
    세대 선별 입력 — API 의 면적/동호 필드 라이브 검증 후 세분화 예정(현재는 미사용, 미검증 스텁)."""
    key = key or os.environ.get("MOLIT_API_KEY", "")
    if not key:
        raise SystemExit("MOLIT_API_KEY 미설정 (공시가도 같은 data.go.kr 키 사용)")
    sgg = lawd_for_district(district)
    if not sgg:
        raise SystemExit(f"시군구코드 미해결: {district}")
    xml_text = _get(GONGSI_EP, {"sidoCode": sgg[:2], "guCode": sgg, "bldNm": complex_name,
                                "numOfRows": 100, "pageNo": 1}, key)
    hits = parse_gongsi(xml_text, complex_name)
    if not hits:
        return None
    return int(statistics.median(h["gongsi_man"] for h in hits))
