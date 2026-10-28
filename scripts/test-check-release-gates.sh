#!/usr/bin/env bash
# Checks for scripts/check-release-gates.sh: a failed run, a pending run, a run
# count that changes during the settle window, a timeout, and a clean pass.
# gh is stubbed from a scenario file (one JSON array per line, one per call) and
# sleep is stubbed to record the requested waits, so no case sleeps. The timeout
# case sets TAG_WAIT_SECONDS=0 so the deadline is already due on the first poll.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
SHA=0123456789abcdef0123456789abcdef01234567
fails=0
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

# want: pass, or "refuse:<words>".
check() {
  local name="$1" want="$2" scenario="$3" got=pass out dir="$work/$4"
  mkdir -p "$dir/bin"
  printf '%s\n' "$scenario" > "$dir/scenario"
  : > "$dir/calls"
  cat > "$dir/bin/gh" <<'EOF'
#!/usr/bin/env bash
printf x >> "$CASE_DIR/calls"
line="$(sed -n "$(( $(wc -c < "$CASE_DIR/calls") ))p" "$CASE_DIR/scenario")"
[[ -n "$line" ]] || line="$(tail -1 "$CASE_DIR/scenario")"
printf '%s\n' "$line"
EOF
  cat > "$dir/bin/sleep" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$1" >> "$CASE_DIR/slept"
EOF
  chmod +x "$dir/bin/gh" "$dir/bin/sleep"
  : > "$dir/slept"
  out=$(CASE_DIR="$dir" PATH="$dir/bin:$PATH" TAG_WAIT_SECONDS="${TAG_WAIT_SECONDS:-90}" TAG_SETTLE_SECONDS=60 \
    ./check-release-gates.sh "${TEST_SHA_OVERRIDE:-$SHA}" 2>&1) || got=crash
  [[ "$got" = crash ]] && [[ "$want" == refuse:* ]] && [[ "$out" == *"${want#refuse:}"* ]] && got="$want"
  if [ "$got" = "$want" ]; then
    echo "ok - $name"
  else
    echo "not ok - $name (got $got, wanted $want): $out"; fails=$((fails + 1))
  fi
}

OK='[{"workflowName":"CI","status":"completed","conclusion":"success","url":"https://x/ok"}]'
PEND='[{"workflowName":"CI","status":"in_progress","conclusion":"","url":"https://x/pend"}]'
BAD='[{"workflowName":"CI","status":"completed","conclusion":"failure","url":"https://x/bad"}]'
TWO='[{"workflowName":"CI","status":"completed","conclusion":"success","url":"https://x/ok"},{"workflowName":"UI","status":"completed","conclusion":"skipped","url":"https://x/skip"}]'

check "a clean pass with one run" pass "$OK" pass1
check "a failing run is refused, named with its url" refuse:"CI: failure https://x/bad" "$BAD" fail1
check "a run still pending eventually passes" pass "$(printf '%s\n%s\n%s\n%s\n' "$PEND" "$PEND" "$OK" "$OK")" pend
check "a new run during settle is waited on, then passes" pass "$(printf '%s\n%s\n%s\n' "$OK" "$TWO" "$TWO")" settle
check "a new run during settle that fails is refused" refuse:"failure" \
  "$(printf '%s\n%s\n' "$OK" '[{"workflowName":"CI","status":"completed","conclusion":"success","url":"u"},{"workflowName":"UI","status":"completed","conclusion":"failure","url":"https://x/bad"}]')" settlefail
TAG_WAIT_SECONDS=0 check "no runs at all times out" refuse:"timed out" '[]' timeout
TEST_SHA_OVERRIDE=notasha check "a malformed SHA is refused" refuse:"expected a full commit SHA" "$OK" badsha
check "a cancelled run is refused" refuse:"cancelled" \
  '[{"workflowName":"CI","status":"completed","conclusion":"cancelled","url":"u"}]' cancelled
check "skipped and neutral count as passing" pass "$(printf '%s\n%s\n' "$TWO" '[{"workflowName":"UI","status":"completed","conclusion":"neutral","url":"u"}]')" skipped

if [ "$fails" -gt 0 ]; then echo "FAILED: $fails check(s)" >&2; exit 1; fi
echo "All check-release-gates.sh checks passed"
