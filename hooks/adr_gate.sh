#!/usr/bin/env bash
# AIOS ADR gate — Claude Code PreToolUse hook.
# Ported 2026-07-10 from /Volumes/EXT_SSD/bot/ai_company/hooks/adr_gate.sh (hooks-only extraction,
# see docs/adr/0001-adopt-adr-gate.md). Only local change: gated dirs += agent_realestate/ package.
# Enforces "Architecture Before Code": a source edit needs a matching ADR in docs/adr/.
# Contract: hook JSON on stdin. exit 0 = allow, exit 2 = block (block mode only).
# Default = warn (logs a gate_miss, never blocks). Set AIOS_GATE_MODE=block to enforce hard.
# Defensive by design: any internal error must NOT block edits — fail open with exit 0.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || exit 0
MODE="${AIOS_GATE_MODE:-warn}"
EVENTS="$ROOT/.aios/observation/events.jsonl"
ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo '?')"
branch="$(git -C "$ROOT" branch --show-current 2>/dev/null || echo '-')"

payload="$(cat 2>/dev/null || true)"
parsed="$(printf '%s' "$payload" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print("_\t_"); sys.exit(0)
t = d.get("tool_name", "_")
ti = d.get("tool_input", {}) or {}
f = ti.get("file_path") or ti.get("path") or "_"
print(f"{t}\t{f}")
' 2>/dev/null || printf '_\t_')"
TOOL="${parsed%%$'\t'*}"
FILE="${parsed#*$'\t'}"

# Only gate source-code edits; never gate framework/docs/tests/config.
# Local scope: the agent_realestate/ package (decision logic). blog/ cron/ site/ stay ungated
# until a real failure demands otherwise (AIOS §Growth rule).
case "$TOOL" in Write|Edit|NotebookEdit) ;; *) exit 0 ;; esac
case "$FILE" in
  "$ROOT"/agent_realestate/*|"$ROOT"/app/*|"$ROOT"/src/*|"$ROOT"/backend/*|"$ROOT"/frontend/*|"$ROOT"/lib/*) ;;
  *) exit 0 ;;
esac
case "$FILE" in *_test.*|*.test.*|*/tests/*|*/__tests__/*) exit 0 ;; esac

# Feature work happens on a feat/<slug|TICKET> branch; an ADR must reference that key.
# On main/master/develop there is no feature context → block app-source edits (branch first).
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

# 3-level outcome: PASS (complete ADR) / WARN (incomplete ADR — allow+nag) / BLOCK (no ADR).
if [ -n "$adr_file" ]; then
  lint="$(bash "$ROOT/bin/adr_lint.sh" "$adr_file" 2>/dev/null | head -1)"
  [ -z "$lint" ] && lint="complete"   # empty = lint could not run → fail-open
  if [ "${lint%%:*}" = "complete" ]; then
    printf '{"ts":"%s","event":"gate_pass","tool":"%s","branch":"%s","file":"%s","adr":"%s"}\n' \
      "$ts" "$TOOL" "$branch" "$FILE" "$(basename "$adr_file")" >> "$EVENTS" 2>/dev/null || true
    exit 0
  fi
  printf '{"ts":"%s","event":"gate_warn","tool":"%s","branch":"%s","file":"%s","adr":"%s","lint":"%s"}\n' \
    "$ts" "$TOOL" "$branch" "$FILE" "$(basename "$adr_file")" "$lint" >> "$EVENTS" 2>/dev/null || true
  echo "AIOS warn: ADR $(basename "$adr_file") is structurally incomplete ($lint). Fill it (docs/adr/_template.md) before/with this edit (allowing this edit)." >&2
  exit 0
fi

printf '{"ts":"%s","event":"gate_miss","tool":"%s","branch":"%s","file":"%s","mode":"%s"}\n' \
  "$ts" "$TOOL" "$branch" "$FILE" "$MODE" >> "$EVENTS" 2>/dev/null || true
msg="AIOS gate — editing code ($FILE) on branch '$branch' with no matching ADR. Use a feat/<slug> branch and add docs/adr/*<slug>*.md (copy docs/adr/_template.md) first (Architecture Before Code)."
if [ "$MODE" = "block" ]; then
  echo "$msg" >&2
  exit 2
fi
echo "AIOS warn: $msg" >&2
exit 0
