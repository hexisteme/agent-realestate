#!/usr/bin/env bash
# 데일리 자동화 thin wrapper — 로직은 `agent-realestate daily` 로 단일화 (Phase 2, 2026-06-11).
# 순서(cli.cmd_daily): MOLIT fresh → 블로그 생성(신선도 게이트) → 사이트 조립 → push-if-changed
#                     → 플래그십 리포트 regen(게이트, 비치명)
# launchd: com.hexisteme.re-blog.daily — 07:05 본실행 + 09/12/15/18/21:05 재시도 슬롯,
#          로그 ~/Library/Logs/re-blog.log (2026-07-06, /tmp 는 재부팅 소실이라 이동)
set -euo pipefail
cd /Volumes/EXT_SSD/bot/agent_realestate
# ★멱등 가드(2026-06-30, 2026-07-06 이원화): RunAtLoad=true·주간 재시도와 짝.
#   사이트 마커(.last-published)와 티스토리 마커(.last-tistory-published)를 분리 —
#   07:05 에 사이트만 성공하고 티스토리가 로그인만료로 실패한 날, 재실행이 사이트는
#   건너뛰고 티스토리만 재시도할 수 있게 한다(기존엔 사이트 마커가 전체를 막아
#   재로그인 후에도 수동 명령 없이는 그날 발행 불가였음). 마커는 EXT_SSD 영속.
TODAY="$(date +%F)"
STAMP="/Volumes/EXT_SSD/bot/agent_realestate/.last-published"
TISTAMP="/Volumes/EXT_SSD/bot/agent_realestate/.last-tistory-published"
if [ "$(cat "$STAMP" 2>/dev/null)" = "$TODAY" ] && [ "$(cat "$TISTAMP" 2>/dev/null)" = "$TODAY" ]; then
  echo "[$(date)] 오늘($TODAY) 사이트+티스토리 모두 발행 완료 — skip (멱등 가드)"
  exit 0
fi
if [ "$(cat "$STAMP" 2>/dev/null)" = "$TODAY" ]; then
  echo "[$(date)] 사이트는 발행 완료 — 티스토리만 재시도"
else
  echo "[$(date)] daily run start"
  python3 -m agent_realestate.cli daily
  # site 발행(cli daily rc=0) 성공 시점에 마커 기록.
  # set -e 라 daily 실패 시 여기 도달 못 함 → 마커 미기록 → 다음 로드에서 재시도(자가복원).
  echo "$TODAY" > "$STAMP"
fi
# 티스토리 자동 공개발행 — 로그인된 Chrome 세션 재사용(2026-06-23 검증).
# 주입→카테고리 '부동산>서울(아파트)' 선택→완료→공개(#open20)→발행(#publish-btn).
# caffeinate -d -i: 07:05 디스플레이/유휴 절전으로 Chrome 렌더러 throttle→-1712 방지(2026-06-24 수정).
# 티스토리 공개발행 — Playwright 퍼블리셔(2026-06-28 재구축, 본문 정상 게시 검증 #9).
#   배경: 구 AppleScript+JS(tistory_publish.py)는 execute javascript 가 isolated-world 라
#   Kakao SPA 에디터 모델(el.CodeMirror)에 접근 못 해 본문 빈 글만 발행했음(#3~#8). Playwright 는
#   main-world + 실제 클립보드 붙여넣기로 기본 kakao 모델을 채움(PASTE_BASIC:78894). dialog.accept
#   로 native confirm 처리. 헤드풀 필수(헤드리스는 Keychain 쿠키 못읽음). 로그인 영속(전용 프로필,
#   '로그인 상태 유지') — 만료 시 --login-wait 30 후 skip(비치명, 빈 글 안 만듦).
# 2026-07-06 자가복구 3종: (1) 카카오 SSO 자동 재로그인, (2) 카카오 keepalive,
# (3) 발행마커+주간 재시도(plist StartCalendarInterval 배열). 성공 마커는 퍼블리셔가 기록.
# --login-wait 120: 무인 재로그인 실패 시 사람이 창을 볼 기회 — 재시도마다 2분.
caffeinate -d -i python3 blog/tistory_publish_pw.py --mode publish --login-wait 120 \
  || echo "[$(date)] tistory_publish_pw skipped (로그인 만료? 재로그인: python3 blog/tistory_publish_pw.py --mode publish --login-wait 600)"
echo "[$(date)] done"
