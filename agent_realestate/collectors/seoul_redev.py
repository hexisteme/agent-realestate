"""R5 재개발/재건축 단계 — 서울 열린데이터광장 정비사업 현황 (무료). SEOUL_OPENAPI_KEY.
data.seoul OpenAPI 형식: http://openapi.seoul.go.kr:8088/{KEY}/json/{SERVICE}/{S}/{E}/
서비스명(OA-2253)은 배포시점에 따라 다를 수 있어 SEOUL_REDEV_SERVICE 로 override (라이브 확인 권장).
단계 텍스트 → RedevStage 매핑 + 단지명 매칭. stdlib 만."""

from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_SERVICE = "tbgisJeongbiSaupInfo"   # OA-2253 추정 서비스명 — 라이브 검증 후 확정

_STAGE_MAP = [
    ("이주", "MOVE_OUT"), ("철거", "MOVE_OUT"), ("착공", "MOVE_OUT"),
    ("관리처분", "MGMT_DISPOSAL"), ("사업시행", "PROJECT_PLAN"),
    ("조합설립", "UNION_SETUP"), ("추진위", "PROMOTION"), ("구역지정", "PROMOTION"),
    ("정비구역", "PROMOTION"), ("안전진단", "SAFETY_PASS"),
]


def map_stage(text: str) -> str:
    t = text or ""
    for kw, stage in _STAGE_MAP:
        if kw in t:
            return stage
    return "NONE"


def parse_seoul_json(json_text: str, service: str = DEFAULT_SERVICE) -> list[dict]:
    """data.seoul 응답 → [{name, stage_text, stage}]. 필드명 다양 → 방어적 추출."""
    data = json.loads(json_text)
    block = data.get(service) or next((v for v in data.values() if isinstance(v, dict) and "row" in v), {})
    rows = block.get("row", []) if isinstance(block, dict) else []
    out = []
    for r in rows:
        name = r.get("SAUP_NM") or r.get("CONT_NM") or r.get("GU_NM") or r.get("name") or ""
        stage_text = r.get("STEP") or r.get("PROGRESS") or r.get("STAGE") or r.get("STTUS") or ""
        out.append({"name": name.strip(), "stage_text": stage_text.strip(),
                    "stage": map_stage(stage_text)})
    return out


def fetch_redev(key: str | None = None, service: str | None = None,
                start: int = 1, end: int = 1000) -> list[dict]:
    key = key or os.environ.get("SEOUL_OPENAPI_KEY", "")
    service = service or os.environ.get("SEOUL_REDEV_SERVICE", DEFAULT_SERVICE)
    if not key:
        raise SystemExit("SEOUL_OPENAPI_KEY 미설정 (data.seoul.go.kr 무료 인증키, .env)")
    url = f"http://openapi.seoul.go.kr:8088/{key}/json/{service}/{start}/{end}/"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            txt = r.read().decode("utf-8")
    except Exception as e:
        raise SystemExit(f"서울 정비사업 요청 실패: {str(e).replace(key, '***KEY***')}")
    return parse_seoul_json(txt, service)
