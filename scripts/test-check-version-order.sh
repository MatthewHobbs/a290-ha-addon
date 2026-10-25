#!/usr/bin/env bash
# Checks for scripts/check-version-order.sh: a release must be above every
# other released tag, compared as numbers, and may re-run for its own tag.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
fails=0
# want: pass, or "refuse:<words>", a non-zero exit whose ::error:: line
# contains those words, so each case fails only for the reason it names.
check() {
  local name="$1" want="$2" version="$3" input="$4" got=pass out
  out=$(printf '%b' "$input" | ./check-version-order.sh "$version" 2>/dev/null) || got=crash
  [ "$got" = crash ] && [[ "$out" == "::error::"* ]] && [[ "$want" == refuse:* ]] &&
    [[ "$out" == *"${want#refuse:}"* ]] && got="$want"
  if [ "$got" = "$want" ]; then echo "ok - $name"; else echo "not ok - $name (got $got, wanted $want: $out)"; fails=$((fails + 1)); fi
}
TAGS='v0.5.8\nv0.5.9\nv0.6.0\n'
check "a version above every tag passes" pass 0.6.1 "${TAGS}v0.6.1\n"
check "a re-run for the latest tag passes" pass 0.6.0 "$TAGS"
check "a version below the latest is refused" refuse:"not above" 0.5.10 "${TAGS}v0.5.10\n"
check "an older version re-run after a newer one is refused" refuse:"not above" 0.5.9 "$TAGS"
check "fields compare as numbers" pass 0.6.10 'v0.6.9\nv0.6.2\nv0.6.10\n'
check "a lower major with a higher minor is refused" refuse:"not above" 0.9.0 'v1.0.0\nv0.9.0\n'
check "tags that are not X.Y.Z are ignored" pass 0.1.0 'vtest\nv1.0\nv0.1.0\n'
check "a first release, its own tag alone, passes" pass 0.1.0 'v0.1.0\n'
check "a list without its own tag is refused (tags not fetched)" refuse:"not in the tag list" 0.6.1 "$TAGS"
check "empty input is refused" refuse:"not in the tag list" 0.1.0 ''
check "a last line without a newline is still checked" refuse:"not above" 0.5.10 'v0.5.10\nv0.6.0'
check "a tag field too long to compare is refused" refuse:"cannot compare" 0.0.2 'v0.0.2\nv0.0.18446744073709551617\n'
check "a version with a leading zero is refused" refuse:"not X.Y.Z" 0.6.01 'v0.6.01\n'
check "a version that is not X.Y.Z is refused" refuse:"not X.Y.Z" 0.6 "$TAGS"
if [ "$fails" -gt 0 ]; then echo "FAILED: $fails check(s)" >&2; exit 1; fi
echo "All check-version-order.sh checks passed"
