"""입지 정량화 (R9). transit 자유문 → 노선수·도보분·GTX·학군 정량 신호 + location_score.
키 없는 결정론 파싱(문자열에 있는 정보만). 도보거리·학군배정 정밀치는 외부 API(키) 영역."""

from __future__ import annotations

import re
from dataclasses import dataclass

_LINE_PAT = re.compile(r"(\d+호선|GTX-?[ABC]|경의중앙|수인분당|신분당|공항철도|우이신설|신림선|경춘|서해|김포골드|동북선|면목선|강북횡단|서부선)")
_WALK_PAT = re.compile(r"도보\s*(\d+)\s*분")
_SCHOOL = ("학군", "은행사거리", "대치", "목동", "중계")


@dataclass(frozen=True)
class LocationProfile:
    line_count: int
    lines: tuple[str, ...]
    walk_min: int | None
    has_gtx: bool
    school_signal: bool
    score: float           # 0~5 정량 입지 점수


def parse_location(transit: str) -> LocationProfile:
    lines = tuple(dict.fromkeys(_LINE_PAT.findall(transit or "")))  # 중복 제거·순서보존
    m = _WALK_PAT.search(transit or "")
    walk = int(m.group(1)) if m else None
    gtx = any("GTX" in x for x in lines) or "GTX" in (transit or "")
    school = any(k in (transit or "") for k in _SCHOOL)
    n = len(lines)
    base = 4.5 if n >= 3 else 3.8 if n == 2 else 3.0 if n == 1 else 2.0
    if walk is not None:
        base += 0.5 if walk <= 5 else 0.3 if walk <= 10 else 0.0
    if gtx:
        base += 0.4
    if school:
        base += 0.3
    return LocationProfile(line_count=n, lines=lines, walk_min=walk, has_gtx=gtx,
                           school_signal=school, score=round(min(5.0, base), 2))


def location_note(lp: LocationProfile) -> str:
    bits = [f"노선 {lp.line_count}개" + (f"({'·'.join(lp.lines)})" if lp.lines else "")]
    if lp.walk_min is not None:
        bits.append(f"도보 {lp.walk_min}분")
    if lp.has_gtx:
        bits.append("GTX")
    if lp.school_signal:
        bits.append("학군")
    return " · ".join(bits) + f" → 입지점수 {lp.score}/5"
