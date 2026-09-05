"""스캔 스코프(11gu/25gu) → run_daily 입력 파일 경로 단일 소스.

2026-09-05 사고: cmd_daily(_cmd_daily_inner)는 RE_SCAN_SCOPE=25gu 일 때 --molit/--jeonse/
--public-frame/--survivors 경로를 cli.py 안에 리터럴로만 박아 blog.run_daily 서브프로세스에 넘겼다.
그런데 blog/run_daily.py 를 인자 없이 단독 실행하면(오늘 실제로 발생) 같은 스코프 정보가 없어
legacy 기본값(molit_recent_11gu_20260606.json, public-frame/survivors 미지정 → 구 universe 경로)
으로 조립됐고, 그 결과(11구·117단지)가 build_site 에 의해 643단지 사이트 위에 얹혀 축소 발행될
뻔했다(발행 전 발견 — 증상 가드 assert_dataset_not_shrunk 를 build_site 에 추가했지만 그건 증상
차단일 뿐 원인 수정이 아니다).

원인: 스코프→입력경로 매핑이 cli.py 안에만 있었다. resolve_scope_inputs 하나로 cli.py._cmd_daily_inner
와 blog/run_daily.py build_arg_parser 양쪽이 항상 같은 값을 보게 한다(단일 소스)."""
from __future__ import annotations

import os
from pathlib import Path


def current_scope() -> str:
    """현재 스캔 스코프 — RE_SCAN_SCOPE 미설정 시 legacy 11gu."""
    return os.environ.get("RE_SCAN_SCOPE", "11gu")


def resolve_scope_inputs(scope: str, root: Path) -> dict:
    """스코프 → run_daily 입력 경로/값.

    "25gu" 가 아닌 값은 전부 legacy 11gu 로 취급한다 — cli.py 의 기존
    `if scope == "25gu": ... else: ...` 분기와 동일 동작이라, 오타·미설정 스코프도 크래시 없이
    11gu 로 안전 폴백한다.

    반환 키:
      molit, jeonse       — Path (25gu 가 아니면 jeonse=None, molit 은 legacy 11gu 경로)
      public_frame, survivors — str (25gu 가 아니면 None — public 경로 자체가 없음)
      public_gu_allow     — str (25gu 일 때만 RE_PUBLIC_GU_ALLOW 를 읽는다·cli.py 의 기존 동작과
                            동일, 아니면 '')
    """
    if scope == "25gu":
        return {
            "molit": root / "examples/molit_recent_25gu_20260710.json",
            "jeonse": root / "examples/molit_jeonse_recent_25gu_20260710.json",
            "public_frame": "examples/frame_25gu_20260710.json",
            "survivors": "examples/screen_25gu_survivors_20260710.json",
            "public_gu_allow": os.environ.get("RE_PUBLIC_GU_ALLOW", ""),
        }
    return {
        "molit": root / "examples/molit_recent_11gu_20260606.json",
        "jeonse": None,
        "public_frame": None,
        "survivors": None,
        "public_gu_allow": "",
    }
