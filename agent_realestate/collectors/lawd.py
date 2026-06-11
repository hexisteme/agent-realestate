"""서울 25개 자치구 법정동코드(LAWD_CD, 시군구 5자리) 매핑 (R12).
MOLIT API 의 LAWD_CD 인자를 단지명/구 이름으로 자동 해결하기 위함."""

from __future__ import annotations

SEOUL_LAWD: dict[str, str] = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200",
    "광진구": "11215", "동대문구": "11230", "중랑구": "11260", "성북구": "11290",
    "강북구": "11305", "도봉구": "11320", "노원구": "11350", "은평구": "11380",
    "서대문구": "11410", "마포구": "11440", "양천구": "11470", "강서구": "11500",
    "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
    "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710",
    "강동구": "11740",
}


def lawd_for_district(name: str) -> str | None:
    """'노원구' 또는 '노원' → '11350'. 미매칭 None."""
    if not name:
        return None
    key = name.strip()
    if key in SEOUL_LAWD:
        return SEOUL_LAWD[key]
    if key + "구" in SEOUL_LAWD:
        return SEOUL_LAWD[key + "구"]
    for gu, code in SEOUL_LAWD.items():           # 부분 포함 (주소 문자열)
        if gu in key:
            return code
    return None
