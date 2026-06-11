"""KB부동산 KB시세 수집 — 로그인된 Chrome 탭 DOM 경유 (2026-05-29 확립).

KB API(`land-complex/serch`·시세)는 로그인 회원 토큰 헤더 필요(`userinfo Error` 33950) —
네이버 overview 가 무인증 공개였던 것과 다르다. 그래서 로그인 세션이 *렌더한* 단지 페이지
(kbland.kr/c/{code})의 시세 블록을 `~/.claude/scripts/kb-sise.sh`(read-chrome-tab.sh 위)로 읽는다.

KB시세 일반가 = 은행 LTV 산정 기준 → 호가·실거래와 함께 3원 비교의 핵심 축.
관련: RDU-124, AGENTS.md 2026-05-29, AGENT_TASKS RE-W1.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

DEFAULT_SCRIPT = "/Users/kimjonghyun/.claude/scripts/kb-sise.sh"


@dataclass(frozen=True)
class KBSise:
    code: str
    name: str | None = None
    mae_ilban_manwon: int | None = None        # KB시세 일반가 (만원) — 은행 LTV 기준
    mae_upper_manwon: int | None = None         # 상위평균가
    mae_lower_manwon: int | None = None         # 하위평균가
    mae_listing_avg_manwon: int | None = None   # 매물평균가 (현재 호가 평균)
    jeonse_ilban_manwon: int | None = None       # 전세 일반가

    @property
    def mae_ilban_krw(self) -> int | None:
        return self.mae_ilban_manwon * 10000 if self.mae_ilban_manwon else None

    @property
    def listing_vs_kb_pct(self) -> float | None:
        """매물평균(호가) ↔ KB시세 일반가 괴리(%). 클수록 호가가 KB기준보다 위."""
        if self.mae_listing_avg_manwon and self.mae_ilban_manwon:
            return round((self.mae_listing_avg_manwon / self.mae_ilban_manwon - 1) * 100, 1)
        return None


def parse_kb_sise(json_str: str) -> KBSise:
    """kb-sise.sh 의 JSON → KBSise (파싱 전용, 테스트 가능)."""
    d = json.loads(json_str) if isinstance(json_str, str) else json_str
    return KBSise(
        code=str(d.get("code", "")), name=d.get("name"),
        mae_ilban_manwon=d.get("mae_ilban_manwon"),
        mae_upper_manwon=d.get("mae_upper_manwon"),
        mae_lower_manwon=d.get("mae_lower_manwon"),
        mae_listing_avg_manwon=d.get("mae_listing_avg_manwon"),
        jeonse_ilban_manwon=d.get("jeonse_ilban_manwon"))


def fetch_kb_sise(code: str, script: str = DEFAULT_SCRIPT) -> KBSise | None:
    """단지코드(kbland.kr/c/{code}) → KBSise. 로그인된 Chrome + kbland 탭 필요. 실패 시 None."""
    try:
        res = subprocess.run([script, str(code)], capture_output=True, text=True, timeout=40)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                return parse_kb_sise(line)
            except (json.JSONDecodeError, ValueError):
                return None
    return None
