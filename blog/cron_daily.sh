#!/usr/bin/env bash
# 데일리 자동화 thin wrapper — 로직은 `agent-realestate daily` 로 단일화 (Phase 2, 2026-06-11).
# 순서(cli.cmd_daily): MOLIT fresh → 블로그 생성(신선도 게이트) → 사이트 조립 → push-if-changed
#                     → 플래그십 리포트 regen(게이트, 비치명)
# launchd: com.hexisteme.re-blog.daily — 07:05 본실행 + 09/12/15/18/21:05 재시도 슬롯,
#          로그 ~/Library/Logs/re-blog.log (2026-07-06, /tmp 는 재부팅 소실이라 이동)
set -Eeuo pipefail   # -E(errtrace): 함수/서브셸 실패도 ERR 트랩으로 (2026-07-11)
ROOT="${AGENT_REALESTATE_ROOT:-/Volumes/EXT_SSD/bot/agent_realestate}"
cd "$ROOT"

# ★재발방지 2종(2026-07-11 무알림 크래시 사고 — grok-4.5 적대검증 채택, AGENTS.md 07-11):
# ① 쉘 최후방어선: python 알림망(cmd_daily 래퍼/step()/티스토리 퍼블리셔)에 도달조차 못 하는
#    실패(인터프리터·디스크·미처리 rc≠0)도 텔레그램 표면화. 시크릿은 .env 에서만 읽음(하드코딩
#    금지). 트랩 내부는 전부 비치명(|| true) — 알림 실패가 트랩 재귀/추가 종료를 못 만들게.
#    한계(정직): EXT_SSD 미마운트면 .env 접근 불가 = 이 경로로도 알림 불가(조용히 return).
_tg_last_resort() {
  local rc="${1:-?}" token="" chat=""
  token=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || true)
  chat=$(grep -m1 '^TELEGRAM_CHAT_ID=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- || true)
  { [ -n "$token" ] && [ -n "$chat" ]; } || return 0
  curl -fsS -m 10 "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" \
    --data-urlencode "text=❌ re-blog cron_daily.sh 비정상 종료 rc=${rc} $(date '+%F %T') — ~/Library/Logs/re-blog.log 확인" \
    >/dev/null 2>&1 || true
}
trap '_tg_last_resort "$?"' ERR
# ② 전역잠금(fcntl.flock): 실행이 3h 슬롯 간격을 넘기면 다음 슬롯과 동시실행 가능(성공마커는
#    종료 후 기록이라 멱등가드가 못 막음 — grok 지적). 커널이 프로세스 종료 시 자동 해제라
#    kill -9 에도 stale 없음. macOS 는 flock(1) CLI 부재 → bash FD9 에 python fcntl 잠금
#    (python 종료 후에도 bash 가 FD9 보유 = 같은 open file description → 스크립트 생존 동안 유지).
exec 9>>"$ROOT/.daily.lock"
if ! python3 -c 'import fcntl,sys
try:
    fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    sys.exit(1)'; then
  echo "[$(date)] 다른 daily 인스턴스 실행 중(전역 flock) — skip"
  exit 0
fi

# ★멱등 가드(2026-06-30, 2026-07-06 이원화): RunAtLoad=true·주간 재시도와 짝.
#   사이트 마커(.last-published)와 티스토리 마커(.last-tistory-published)를 분리 —
#   07:05 에 사이트만 성공하고 티스토리가 로그인만료로 실패한 날, 재실행이 사이트는
#   건너뛰고 티스토리만 재시도할 수 있게 한다(기존엔 사이트 마커가 전체를 막아
#   재로그인 후에도 수동 명령 없이는 그날 발행 불가였음). 마커는 EXT_SSD 영속.
TODAY="$(date +%F)"
STAMP="$ROOT/.last-published"
TISTAMP="$ROOT/.last-tistory-published"
# 기간 결산(주간결산/월간결산, 2026-09-07): run_daily 가 일요일에 periodic 원고를 쓰면 2편째 발행 대상 — kind 별 마커.
PERSTAMP="$ROOT/.last-tistory-published-periodic"
PERDRAFT="$ROOT/report/blog/tistory/${TODAY}-periodic-tistory-draft.html"
# 누락 마커는 미발행 상태다. 읽기 실패가 ERR 트랩의 장애 알림을 부르지 않게 한다.
periodic_pending() { [ -f "$PERDRAFT" ] && [ "$(cat "$PERSTAMP" 2>/dev/null || true)" != "$TODAY" ]; }
if [ "$(cat "$STAMP" 2>/dev/null || true)" = "$TODAY" ] && [ "$(cat "$TISTAMP" 2>/dev/null || true)" = "$TODAY" ] && ! periodic_pending; then
  echo "[$(date)] 오늘($TODAY) 사이트+티스토리 모두 발행 완료 — skip (멱등 가드)"
  exit 0
fi
if [ "$(cat "$STAMP" 2>/dev/null || true)" = "$TODAY" ]; then
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
# 2026-09-23: SSO 비밀번호 폼은 macOS Keychain 폴백으로 1회 재인증. Keychain 부재·거부·
# 계정 선택·추가 인증은 기존 사람 대기로 닫으며 비밀값은 argv·환경·로그에 남기지 않는다.
# --login-wait 120: 무인 재로그인 실패 시 사람이 창을 볼 기회 — 재시도마다 2분.
publish_rc=0
publish_kind() {
  local kind="$1" rc
  shift
  if caffeinate -d -i python3 blog/tistory_publish_pw.py --mode publish --login-wait 120 "$@"; then
    rc=0
  else
    rc=$?
  fi
  case "$rc" in
    0) echo "[$(date)] tistory $kind: 완료 또는 기발행" ;;
    3)
      echo "[$(date)] tistory $kind: 사람 검토 대기 (rc=3)"
      [ "$publish_rc" -ne 0 ] || publish_rc=3
      ;;
    4)
      echo "[$(date)] tistory $kind: 브라우저 프로필 사용 중 (rc=4, 다음 슬롯 재시도)"
      [ "$publish_rc" -eq 1 ] || publish_rc=4
      ;;
    *)
      echo "[$(date)] tistory $kind: 발행 실패 (rc=$rc, 다음 슬롯 재시도)"
      publish_rc=1
      ;;
  esac
}
publish_kind daily --pending
# 2편째: 주간결산/월간결산(일요일에만 원고가 생긴다). daily 마커와 독립(.last-tistory-published-periodic).
if periodic_pending; then
  publish_kind periodic --kind periodic
fi
echo "[$(date)] done (publish rc=$publish_rc)"
# 위에서 분류한 퍼블리셔 결과는 Python 알림 경로가 처리한다. 상류 daily 실패의
# ERR 최후 알림은 유지하되, 이 명시 종료에서는 같은 장애를 다시 알리지 않는다.
trap - ERR
exit "$publish_rc"
