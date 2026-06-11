#!/usr/bin/env bash
# 데일리 자동화 thin wrapper — 로직은 `agent-realestate daily` 로 단일화 (Phase 2, 2026-06-11).
# 순서(cli.cmd_daily): MOLIT fresh → 블로그 생성(신선도 게이트) → 사이트 조립 → push-if-changed
#                     → 플래그십 리포트 regen(게이트, 비치명)
# cron: 5 7 * * * /Volumes/EXT_SSD/bot/agent_realestate/blog/cron_daily.sh >> /tmp/re-blog.log 2>&1
set -euo pipefail
cd /Volumes/EXT_SSD/bot/agent_realestate
echo "[$(date)] daily run start"
python3 -m agent_realestate.cli daily
echo "[$(date)] done"
