"""단지 검색 → 이동 — 수집 전 *탭 위치 자동화* (2026-05-29 확립, 수동 네비 개입 제거).

배경: per-매물 4요소·DOM 수집(naver_live)은 Chrome 탭이 *그 단지 페이지* 에 있어야 작동했고,
거기로 이동하는 건 사용자 수동 검색·클릭이었다(개입의 주원인). new.land.naver.com 검색
자동완성은 keyup-driven(fragile, RDU-024 레시피)이라, 더 견고한 경로 = 내부 `/api/search`
로 단지명→complexNo 해석 후 URL 내비(drift 0). `~/.claude/scripts/naver-nav.sh`(인증 Chrome
페이지컨텍스트 XHR) 가 반환한 JSON 을 파싱한다. 헤드리스 429 회피는 overview 와 동일 원리.
관련: RDU-024(자동완성 레시피는 fallback), naver_overview.py, AGENTS.md 2026-05-29.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

DEFAULT_SCRIPT = "/Users/kimjonghyun/.claude/scripts/naver-nav.sh"


@dataclass(frozen=True)
class NavResult:
    query: str
    found: bool
    complex_no: str | None = None
    name: str | None = None
    navigated_to: str | None = None


def parse_nav(json_str: str) -> NavResult:
    """naver-nav.sh 의 JSON → NavResult (파싱 전용, 테스트 가능)."""
    d = json.loads(json_str) if isinstance(json_str, str) else json_str
    return NavResult(
        query=d.get("query", ""), found=bool(d.get("found")),
        complex_no=(str(d["complexNo"]) if d.get("complexNo") is not None else None),
        name=d.get("complexName"), navigated_to=d.get("navigatedTo"),
    )


def navigate_to_complex(name: str, script: str = DEFAULT_SCRIPT) -> NavResult:
    """단지명 → 열린 new.land 탭을 그 단지로 이동. Chrome/탭 없거나 검색 실패면 found=False
    (graceful — 추정으로 메우지 않음, G1). 이후 naver_live/overview 수집이 그 탭 위에서 작동."""
    if not name or not name.strip():
        return NavResult(query=name or "", found=False)
    try:
        res = subprocess.run([script, name.strip()], capture_output=True, text=True, timeout=40)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return NavResult(query=name, found=False)
    if res.returncode != 0 or not res.stdout.strip():
        return NavResult(query=name, found=False)
    try:
        return parse_nav(res.stdout.strip())
    except (json.JSONDecodeError, ValueError):
        return NavResult(query=name, found=False)
