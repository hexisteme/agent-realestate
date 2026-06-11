"""경사도 근사 — opentopodata SRTM 30m (무료·무키). 카카오엔 고도 API 부재.
중심±150m 4방(N/S/E/W) 최대 기울기%. SRTM 30m 격자 한계 → 실제 보행 체감과 다를 수 있는
[추론] 근사. report-time 아닌 오프라인 수집 단계에서만 호출(G3 결정론 보존)."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request

_URL = "https://api.opentopodata.org/v1/srtm30m"
_DLAT = 0.00135   # ≈150m 위도
_DLNG = 0.0017    # ≈150m 경도(위도 37.5° 근사)


def compute_slope(lat: float, lng: float) -> dict | None:
    """(lat,lng) 중심 ±150m 4방 표고 → 최대 기울기%·기복·등급. 실패 시 None."""
    pts = [(lat, lng), (lat + _DLAT, lng), (lat - _DLAT, lng),
           (lat, lng + _DLNG), (lat, lng - _DLNG)]
    locs = "|".join(f"{a},{b}" for a, b in pts)
    try:
        with urllib.request.urlopen(f"{_URL}?locations={urllib.parse.quote(locs)}", timeout=30) as r:
            els = [p["elevation"] for p in json.loads(r.read().decode("utf-8"))["results"]]
    except Exception:
        return None
    if any(e is None for e in els):
        return None
    c = els[0]
    slope = round(max(abs(e - c) for e in els[1:]) / 150 * 100, 1)
    grade = "가파름" if slope >= 12 else ("완경사" if slope >= 5 else "평탄")
    return {"center_elev_m": round(c, 1), "relief_m": round(max(els) - min(els), 1),
            "slope_pct": slope, "slope_grade": grade}
