"""네이버 단지 overview 수집 — 인증된 Chrome 컨텍스트 경유 (2026-05-29 확립).

배경: new.land.naver.com/api/* 는 헤드리스(curl/python)로는 429 봇차단, 로그인된 Chrome 탭의
페이지 컨텍스트 동기 XHR 로는 200. `/api/complexes/overview/{no}` 는 무인증 공개 — 현재 호가범위
+ 최근 실거래(내장) + 재건축여부 + 세대/준공/좌표를 한 번에 준다. 호가↔실거래 괴리를 단지단위로.

이 모듈은 `~/.claude/scripts/naver-overview.sh`(read-chrome-tab.sh 위) 가 반환한 JSON 을 파싱한다.
per-매물 4요소(동·호/층/향/중개사/확인일)는 article API(bearer 토큰)·또는 매물탭 DOM 읽기 영역 — 별도.
관련: RDU-124(불가능 선언 전 방법 계층 전환), AGENTS.md 2026-05-29.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

DEFAULT_SCRIPT = "/Users/kimjonghyun/.claude/scripts/naver-overview.sh"


@dataclass(frozen=True)
class ComplexOverview:
    query: str
    found: bool
    complex_no: str | None = None
    name: str | None = None
    type: str | None = None              # 재건축 | 아파트 ...
    households: int | None = None
    built_year: int | None = None
    asking_min_manwon: int | None = None  # 현재 호가 하한 (만원)
    asking_max_manwon: int | None = None
    asking_min: str | None = None         # "8억 1,000"
    asking_max: str | None = None
    recent_deal_manwon: int | None = None  # 최근 실거래 (만원)
    recent_deal: str | None = None
    recent_deal_ymd: str | None = None     # "2026.05.07"
    recent_deal_area_m2: float | None = None
    lat: float | None = None
    lng: float | None = None

    @property
    def gap_pct(self) -> float | None:
        """호가하한 ↔ 최근실거래 괴리(%). 클수록 호가가 검증가격보다 위 = 미검증·고위험."""
        if self.asking_min_manwon and self.recent_deal_manwon:
            return round((self.asking_min_manwon / self.recent_deal_manwon - 1) * 100, 1)
        return None


def _to_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def parse_overview(json_str: str) -> list[ComplexOverview]:
    """naver-overview.sh 의 JSON 배열 → ComplexOverview 리스트. 파싱 전용(테스트 가능)."""
    data = json.loads(json_str) if isinstance(json_str, str) else json_str
    out: list[ComplexOverview] = []
    for r in data:
        if not r.get("found"):
            out.append(ComplexOverview(query=r.get("query", ""), found=False))
            continue
        out.append(ComplexOverview(
            query=r.get("query", ""), found=True, complex_no=str(r.get("complexNo") or ""),
            name=r.get("complexName"), type=r.get("type"),
            households=_to_int(r.get("households")),
            built_year=_to_int((r.get("builtYmd") or "")[:4]),
            asking_min_manwon=_to_int(r.get("askingMinManwon")),
            asking_max_manwon=_to_int(r.get("askingMaxManwon")),
            asking_min=r.get("askingMin"), asking_max=r.get("askingMax"),
            recent_deal_manwon=_to_int(r.get("recentDealManwon")),
            recent_deal=r.get("recentDeal"), recent_deal_ymd=r.get("recentDealYmd"),
            recent_deal_area_m2=(float(r["recentDealAreaM2"]) if r.get("recentDealAreaM2") else None),
            lat=r.get("lat"), lng=r.get("lng")))
    return out


def fetch_overview(names: list[str], script: str = DEFAULT_SCRIPT) -> list[ComplexOverview]:
    """단지명 리스트 → ComplexOverview. Chrome(new.land 탭) 실행 + JS 허용 필요.
    Chrome/탭 없으면 빈 리스트 (graceful)."""
    if not names:
        return []
    try:
        res = subprocess.run([script, ",".join(names)], capture_output=True, text=True, timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if res.returncode != 0 or not res.stdout.strip():
        return []
    try:
        return parse_overview(res.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return []
