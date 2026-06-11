#!/bin/bash
# agent_realestate cron core — Static 계층 신선도 점검 (standalone, mount-guarded, no oMLX).
# 정책/재건축 캐시는 Claude-MCP(scan-policy/update-redev)가 채운다. 이 잡은 *신선도 경보*만.
set -euo pipefail
ROOT="/Volumes/EXT_SSD/bot/agent_realestate"
[ -d "/Volumes/EXT_SSD" ] || { echo "EXT_SSD 미연결 — 중단"; exit 0; }
cd "$ROOT" || exit 0
DB="data/realestate_cache.sqlite"
[ -f "$DB" ] || { echo "캐시 없음 — scan-policy 먼저 실행"; exit 0; }
# 30일 넘은 정책 스냅샷 경보
sqlite3 "$DB" "SELECT topic, MAX(confirmed_date) FROM policy_snapshot GROUP BY topic
  HAVING julianday('now') - julianday(MAX(confirmed_date)) > 30;" 2>/dev/null \
  | while read -r line; do echo "[정책 신선도 경보] 30일+ 미갱신: $line"; done
echo "freshness check done $(date '+%F %T')"
