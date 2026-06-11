"""네이버 *지역 단위* 단지 자동 열거 — 인증된 Chrome 컨텍스트 경유 (2026-05-29 확립).

배경: overview/parse-naver 는 "단지명을 주면 그 단지를 가져오는" 구조라 후보를 스스로 찾지 못했다.
single-markers/2.0 마커 API(무인증 200, 페이지 컨텍스트)는 구/동 bbox 안의 *모든 단지*를
용적률(floorAreaRatio)·준공·세대수와 함께 반환한다 → 지역을 통째로 열거하고 재건축 후보를
필터할 수 있다. 이 모듈은 `~/.claude/scripts/naver-region-scan.sh` 가 반환한 JSON 을 파싱한다.

per-매물 4요소(동·층·향·중개사)는 여전히 parse-naver(매물탭 DOM) 영역 — 본 모듈은 *후보 열거*까지.
관련: RDU-124(불가능 선언 전 방법 계층 전환), AGENTS.md 2026-05-29.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

DEFAULT_SCRIPT = "/Users/kimjonghyun/.claude/scripts/naver-region-scan.sh"

# 서울 25개 구 → cortarNo (법정동코드 prefix + 000000). 구 이름으로도 스캔 가능하게.
SEOUL_GU: dict[str, str] = {
    "종로": "1111000000", "중구": "1114000000", "용산": "1117000000", "성동": "1120000000",
    "광진": "1121000000", "동대문": "1123000000", "중랑": "1126000000", "성북": "1129000000",
    "강북": "1130500000", "도봉": "1132000000", "노원": "1135000000", "은평": "1138000000",
    "서대문": "1141000000", "마포": "1144000000", "양천": "1147000000", "강서": "1150000000",
    "구로": "1153000000", "금천": "1154000000", "영등포": "1156000000", "동작": "1159000000",
    "관악": "1162000000", "서초": "1165000000", "강남": "1168000000", "송파": "1171000000",
    "강동": "1174000000",
}


@dataclass(frozen=True)
class RegionComplex:
    complex_no: str
    name: str
    far_pct: int            # 용적률 (floorAreaRatio; 0 = 미기재)
    built_ym: str           # "199009"
    households: int
    dongs: int | None = None
    min_area_m2: float | None = None
    max_area_m2: float | None = None
    lat: float | None = None
    lng: float | None = None
    district: str | None = None   # 스캔 구 (실제 소재 구와 다를 수 있음 — bbox bleed)

    @property
    def built_year(self) -> int | None:
        try:
            return int(self.built_ym[:4])
        except (TypeError, ValueError):
            return None


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_region(json_str, district: str | None = None) -> list[RegionComplex]:
    """naver-region-scan.sh 의 JSON 배열 → RegionComplex 리스트. 파싱 전용(테스트 가능)."""
    data = json.loads(json_str) if isinstance(json_str, str) else json_str
    if isinstance(data, dict):   # {error: ...}
        return []
    out: list[RegionComplex] = []
    for r in data:
        out.append(RegionComplex(
            complex_no=str(r.get("complexNo") or ""), name=r.get("name") or "",
            far_pct=_to_int(r.get("far")) or 0, built_ym=str(r.get("builtYm") or ""),
            households=_to_int(r.get("households")) or 0, dongs=_to_int(r.get("dongs")),
            min_area_m2=(float(r["minArea"]) if r.get("minArea") else None),
            max_area_m2=(float(r["maxArea"]) if r.get("maxArea") else None),
            lat=r.get("lat"), lng=r.get("lng"), district=district))
    return out


def scan_region(district: str, far_max: int = 9999, built_max_ym: int = 999912,
                hh_min: int = 0, script: str = DEFAULT_SCRIPT) -> list[RegionComplex]:
    """구 이름('노원') 또는 cortarNo('1135000000') → 재건축 후보 단지 자동 열거.
    far_max 이하 용적률 · built_max_ym 이전 준공 · hh_min 이상 세대. Chrome 없으면 빈 리스트."""
    cortar = SEOUL_GU.get(district, district)
    try:
        res = subprocess.run([script, cortar, str(far_max), str(built_max_ym), str(hh_min)],
                             capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        return parse_region(res.stdout.strip(), district=district)
    except (json.JSONDecodeError, ValueError):
        return []
