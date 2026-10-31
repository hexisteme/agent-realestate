#!/bin/bash
# 주간 청년안심주택 모집공고 정리(토요일 공개 발행) + /180 용산 두 곳 공고 감시(매일).
# launchd: ~/Library/LaunchAgents/com.hexisteme.re-blog.youth-weekly.plist (사용자 결정 2026-10-31)
# 무인 발행은 결정론 파이썬만 쓴다(LLM 없음). 캡차는 탭을 남기고 끝내며, captcha-relay 가 텔레그램으로 사람 답을 받는다.
set -u
ROOT=/Volumes/EXT_SSD/bot/agent_realestate
[ -d "$ROOT/blog" ] || { echo "[$(date '+%F %T')] EXT_SSD 미마운트 — skip"; exit 0; }
cd "$ROOT" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
export TISTORY_CDP_URL="${TISTORY_CDP_URL:-http://127.0.0.1:9334}"
# 공개된 글 영수증을 마케팅 원장(MP-flokerblog-01)에 넣는다 — 이 래퍼에서만 켠다(사용자 지시 2026-10-31).
export RE_YOUTH_MARKETING_RECEIPT="${RE_YOUTH_MARKETING_RECEIPT:-1}"
PUB="--publish"
[ "${RE_YOUTH_WEEKLY_PUBLISH:-1}" = "1" ] || PUB=""
if [ -f "$ROOT/.tistory-paused" ]; then
  echo "[$(date '+%F %T')] 티스토리 일시중지(.tistory-paused) — 감시만"
  PUB=""
fi
echo "[$(date '+%F %T')] youth-weekly start ${PUB:-(no publish)}"
python3 -m blog.youth_rental_weekly run $PUB
rc=$?
echo "[$(date '+%F %T')] youth-weekly rc=$rc"
exit $rc
