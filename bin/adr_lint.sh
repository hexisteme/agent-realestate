#!/usr/bin/env bash
# AIOS ADR lint — DETERMINISTIC structural completeness (not semantic quality scoring).
# exit 0 = complete ("complete"); exit 1 = incomplete ("incomplete:<reasons>").
# Fail-open: on any internal trouble, treat as complete so it never blocks work by accident.
set -u
f="${1:-}"
[ -n "$f" ] && [ -f "$f" ] || { echo "complete"; exit 0; }   # can't judge → don't penalize
reasons=""

# ≥2 alternatives: bullet lines under a "## Alternatives*" heading
alts="$(awk '/^## Alternatives/{f=1;next} /^## /{f=0} f&&/^[[:space:]]*-[[:space:]]/{c++} END{print c+0}' "$f" 2>/dev/null || echo 0)"
[ "${alts:-0}" -ge 2 ] 2>/dev/null || reasons="$reasons alts<2"

# named section has at least one non-empty content line
sec_nonempty(){ awk -v h="$1" 'index($0,"## "h)==1{f=1;next} /^## /{f=0} f&&NF{print;exit}' "$f" 2>/dev/null | grep -q .; }
sec_nonempty "Decision"     || reasons="$reasons decision-empty"
sec_nonempty "Consequences" || reasons="$reasons consequences-empty"
sec_nonempty "Evidence"     || reasons="$reasons evidence-empty"

# raw template placeholders left in → incomplete
grep -qE '<title>|<NNNN>|Sources cited \(official docs first\)|<e\.g\.' "$f" 2>/dev/null && reasons="$reasons template-placeholder"

if [ -n "$reasons" ]; then echo "incomplete:${reasons# }"; exit 1; fi
echo "complete"; exit 0
