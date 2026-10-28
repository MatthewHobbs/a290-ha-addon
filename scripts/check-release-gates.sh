#!/usr/bin/env bash
# Wait until every workflow run on $1 (a commit) has completed with a passing
# conclusion. Refuses on any failure, on a timeout, or on an empty set.
#
# `gh run list --commit` is the source of truth. A workflow registers its run a
# few seconds after the push, so an empty pending set is not proof every gate
# ran: the wait settles for TAG_SETTLE_SECONDS (default 60) and requires the run
# count to be unchanged across that window before it passes. Passing conclusions
# are success, skipped and neutral. Seconds are bash SECONDS (monotonic), so the
# timeout does not follow a wall-clock correction.
#
# Env: TAG_WAIT_SECONDS (default 1800), TAG_SETTLE_SECONDS (default 60).
# Self-test: scripts/test-check-release-gates.sh stubs gh and sleep.
set -euo pipefail
sha="${1:-}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo "expected a full commit SHA, got '$sha'"; exit 1; }
echo "Waiting for workflows on ${sha} (timeout ${TAG_WAIT_SECONDS:-1800}s)..."
deadline=$(( SECONDS + ${TAG_WAIT_SECONDS:-1800} ))
while :; do
  (( SECONDS < deadline )) || { echo "timed out waiting for workflows on ${sha}"; exit 1; }
  runs="$(gh run list --commit "$sha" --limit 50 --json workflowName,status,conclusion,url)"
  failed="$(jq -r '.[] | select(.status == "completed" and
    (.conclusion | IN("success","skipped","neutral") | not)) |
    "\(.workflowName): \(.conclusion) \(.url)"' <<<"$runs")"
  [[ -z "$failed" ]] || { echo "$failed"; exit 1; }
  count="$(jq 'length' <<<"$runs")"
  pending="$(jq '[.[] | select(.status != "completed")] | length' <<<"$runs")"
  if (( count > 0 && pending == 0 )); then
    sleep "${TAG_SETTLE_SECONDS:-60}"
    settled="$(gh run list --commit "$sha" --limit 50 --json workflowName)"
    if [[ "$(jq 'length' <<<"$settled")" == "$count" ]]; then break; fi
    continue
  fi
  sleep 20
done
