#!/usr/bin/env bash
# AIOS ADR audit — Stop hook. Backstops the PreToolUse gate for edits it never saw
# (e.g. delegated/subagent builds; AIOS failure F-0002). At session end it scans the working tree:
# source changes under agent_realestate/ (or app/src/backend/frontend/lib) on a branch with no
# matching COMPLETE ADR → flag.
# Ported 2026-07-10 from ai_company/hooks/adr_audit.sh; only local change: source-dir pattern.
# FLAG-only by design (always exit 0): the PreToolUse gate is the blocker; blocking session-end is
# user-hostile and stop-loop-prone. Fail open.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || exit 0
EVENTS="$ROOT/.aios/observation/events.jsonl"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo '?')"
branch="$(git -C "$ROOT" branch --show-current 2>/dev/null || echo '-')"

# changed + untracked source files (exclude tests)
files="$(git -C "$ROOT" status --porcelain 2>/dev/null | sed 's/^...//' \
  | grep -E '^(agent_realestate|app|src|backend|frontend|lib)/' \
  | grep -vE '(_test\.|\.test\.|/tests/|/__tests__/)' || true)"
[ -z "$files" ] && exit 0   # no source changes → nothing to audit

# matching complete ADR for the current branch?
key=""
case "$branch" in
  main|master|develop|-|"") key="" ;;
  *)
    key="$(printf '%s' "$branch" | grep -oE '[A-Z]+-[0-9]+' | head -1 2>/dev/null || true)"
    [ -z "$key" ] && key="$(printf '%s' "$branch" | sed -E 's#^[^/]+/##; s#[/ ].*$##' 2>/dev/null || true)"
    ;;
esac
adr_file=""
if [ -n "$key" ]; then
  adr_file="$(ls "$ROOT"/docs/adr/*"$key"*.md 2>/dev/null | head -1)"
  [ -z "$adr_file" ] && adr_file="$(grep -ril -- "$key" "$ROOT"/docs/adr/ 2>/dev/null | head -1)"
fi
complete=0
if [ -n "$adr_file" ]; then
  lint="$(bash "$ROOT/bin/adr_lint.sh" "$adr_file" 2>/dev/null | head -1)"
  [ "${lint%%:*}" = "complete" ] && complete=1
fi

n="$(printf '%s\n' "$files" | grep -c . 2>/dev/null || echo 0)"
if [ "$complete" = "1" ]; then
  printf '{"ts":"%s","event":"gate_audit_pass","branch":"%s","source_files":%s,"adr":"%s"}\n' \
    "$ts" "$branch" "$n" "$(basename "$adr_file")" >> "$EVENTS" 2>/dev/null || true
  exit 0
fi
printf '{"ts":"%s","event":"gate_audit_miss","branch":"%s","source_files":%s}\n' \
  "$ts" "$branch" "$n" >> "$EVENTS" 2>/dev/null || true
echo "AIOS audit: $n source file(s) changed on branch '$branch' with no matching complete ADR — Architecture-Before-Code may have been bypassed (e.g. a delegated build). Add/complete docs/adr/*<slug>*.md, or move the work onto a feat/<slug> branch." >&2
exit 0
