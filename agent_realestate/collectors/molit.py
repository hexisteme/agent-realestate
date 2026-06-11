"""MOLIT 아파트 매매 실거래가 직접 수집 (② 자동수집). stdlib 만 (urllib + ElementTree).

공공데이터포털 국토교통부_아파트매매 실거래 상세자료
  GET http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev
  params: serviceKey, LAWD_CD(법정동 5자리), DEAL_YMD(YYYYMM), numOfRows, pageNo
서비스키는 env MOLIT_API_KEY. 키는 에러 메시지에서 마스킹한다.
"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ENDPOINT = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"


def _t(item: ET.Element, tag: str) -> str:
    el = item.find(tag)
    return (el.text or "").strip() if el is not None else ""


def parse_items(xml_text: str) -> list[dict]:
    """MOLIT XML → [{complex_name, area_exclusive_m2, deal_ym, price_krw, floor, umd}]."""
    root = ET.fromstring(xml_text)
    out: list[dict] = []
    for item in root.iter("item"):
        amount = _t(item, "dealAmount").replace(",", "").strip()
        if not amount:
            continue
        y, m = _t(item, "dealYear"), _t(item, "dealMonth")
        area = _t(item, "excluUseAr")
        name = _t(item, "aptNm") or _t(item, "aptName")
        out.append({
            "complex_name": name,
            "area_exclusive_m2": float(area) if area else 0.0,
            "deal_ym": f"{y}-{int(m):02d}" if y and m else "",
            "price_krw": int(amount) * 10_000,   # 만원 → 원
            "floor": _t(item, "floor"),
            "umd": _t(item, "umdNm"),
        })
    return out


def fetch_apt_trades(lawd_cd: str, deal_ym: str, service_key: str | None = None,
                     num_rows: int = 1000) -> list[dict]:
    key = service_key or os.environ.get("MOLIT_API_KEY", "")
    if not key:
        raise SystemExit("MOLIT_API_KEY 미설정 — 공공데이터포털 서비스키 필요(env MOLIT_API_KEY)")
    qs = urllib.parse.urlencode({
        "serviceKey": key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ym,
        "numOfRows": num_rows, "pageNo": 1,
    })
    try:
        with urllib.request.urlopen(f"{ENDPOINT}?{qs}", timeout=20) as resp:
            xml_text = resp.read().decode("utf-8")
    except Exception as e:
        msg = str(e).replace(key, "***KEY***")          # 키 마스킹
        raise SystemExit(f"MOLIT 요청 실패: {msg}")
    return parse_items(xml_text)


def filter_by_complex(items: list[dict], complex_name: str) -> list[dict]:
    """단지명 부분일치 필터 (MOLIT 는 법정동 전체를 반환)."""
    key = complex_name.replace(" ", "")
    return [it for it in items if key in it["complex_name"].replace(" ", "")]
