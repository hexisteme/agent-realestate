"""R9 입지 정량 — 카카오 로컬 REST API (무료 10만/일). KAKAO_REST_KEY.
주소 → 좌표(지오코딩) → 최근접 지하철역(SW8) + 거리 → 도보분 근사 → transit 문자열 생성.
이 문자열을 candidate.transit 에 넣으면 analysts/location.py 가 노선·도보를 파싱한다. stdlib 만."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

_GEOCODE = "https://dapi.kakao.com/v2/local/search/address.json"
_CATEGORY = "https://dapi.kakao.com/v2/local/search/category.json"
WALK_M_PER_MIN = 67          # 도보 속도 근사 (≈4km/h)


def _req(url: str, params: dict, key: str) -> dict:
    full = f"{url}?{urllib.parse.urlencode(params)}"
    r = urllib.request.Request(full, headers={"Authorization": f"KakaoAK {key}"})
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise SystemExit(f"카카오 요청 실패: {str(e).replace(key, '***KEY***')}")


def geocode(address: str, key: str) -> tuple[str, str] | None:
    docs = _req(_GEOCODE, {"query": address}, key).get("documents", [])
    if not docs:
        return None
    return docs[0]["x"], docs[0]["y"]      # (lng, lat)


def nearest_subway(x: str, y: str, key: str, radius: int = 1200) -> dict | None:
    docs = _req(_CATEGORY, {"category_group_code": "SW8", "x": x, "y": y,
                            "radius": radius, "sort": "distance", "size": 1}, key).get("documents", [])
    if not docs:
        return None
    d = docs[0]
    return {"name": d["place_name"], "distance_m": int(d["distance"])}


def build_transit(address: str, key: str | None = None) -> str | None:
    """주소 → '○○역 도보 N분' 문자열 (location.py 가 파싱). 미발견 None."""
    key = key or os.environ.get("KAKAO_REST_KEY", "")
    if not key:
        raise SystemExit("KAKAO_REST_KEY 미설정 (developers.kakao.com REST 키, .env)")
    xy = geocode(address, key)
    if not xy:
        return None
    st = nearest_subway(xy[0], xy[1], key)
    if not st:
        return None
    walk = max(1, round(st["distance_m"] / WALK_M_PER_MIN))
    return f"{st['name']} 도보 {walk}분"


def walk_min_from_distance(distance_m: int) -> int:
    return max(1, round(distance_m / WALK_M_PER_MIN))


# ── R9b 입지 정량 확장 (역+학교+학원, 사용자 요청 2026-05-30) ──
_KEYWORD = "https://dapi.kakao.com/v2/local/search/keyword.json"


def geocode_keyword(query: str, key: str, district: str | None = None) -> tuple[str, str, str, str] | None:
    """단지명 키워드 → (lng, lat, place_name, address). district 로 동명 충돌 회피."""
    docs = _req(_KEYWORD, {"query": query, "size": 15}, key).get("documents", [])
    cand = [d for d in docs if (not district or district in d.get("address_name", ""))]
    pick = next((d for d in cand if "아파트" in d.get("category_name", "")), None) or (cand[0] if cand else None)
    if not pick:
        return None
    return pick["x"], pick["y"], pick.get("place_name", ""), pick.get("address_name", "")


def nearest_schools(x: str, y: str, key: str, radius: int = 2000) -> dict:
    """최근접 초/중/고 각 1개 (직선거리 m). category SC4."""
    docs = _req(_CATEGORY, {"category_group_code": "SC4", "x": x, "y": y,
                            "radius": radius, "sort": "distance", "size": 15}, key).get("documents", [])
    out: dict[str, dict] = {}
    for kind in ("초등학교", "중학교", "고등학교"):
        for d in docs:
            if d["category_name"].endswith(kind):
                out[kind] = {"name": d["place_name"], "distance_m": int(d["distance"])}
                break
    return out


def academy_count(x: str, y: str, key: str, radius: int = 500) -> int:
    """반경 내 학원(AC5) 총수 — 태권도·음악·미술 포함. 학군 'quality' 아닌 밀집도 [추론] proxy."""
    meta = _req(_CATEGORY, {"category_group_code": "AC5", "x": x, "y": y,
                            "radius": radius, "size": 1}, key).get("meta", {})
    return int(meta.get("total_count", 0))


def academy_exam_count(x: str, y: str, key: str, radius: int = 800) -> int:
    """입시·보습 학원 수(키워드) — 전체 AC5 중 학습 관련만. 학군 proxy 정밀화(태권도/음악 제외)."""
    meta = _req(_KEYWORD, {"query": "입시학원", "x": x, "y": y,
                           "radius": radius, "size": 1}, key).get("meta", {})
    return int(meta.get("total_count", 0))
