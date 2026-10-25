#!/usr/bin/env bash
# Refuse to release X.Y.Z ($1) unless it is above every other released tag.
# Tags come on stdin, one per line (`git tag -l 'v*'`), and must include v$1,
# which catches an empty or foreign list. The same version is skipped, so
# re-running a release for its own tag still passes.
set -euo pipefail
version="$1"
f='(0|[1-9][0-9]{0,8})'
strict="^$f\.$f\.$f\$"
loose='^[0-9]+\.[0-9]+\.[0-9]+$'
[[ "$version" =~ $strict ]] || { echo "::error::'$version' is not X.Y.Z"; exit 1; }

# 0 if $1 > $2, both X.Y.Z; compared as numbers, so 0.5.10 is above 0.5.9.
above() {
  local a b i
  IFS=. read -ra a <<<"$1"
  IFS=. read -ra b <<<"$2"
  for i in 0 1 2; do
    ((10#${a[i]} > 10#${b[i]})) && return 0
    ((10#${a[i]} < 10#${b[i]})) && return 1
  done
  return 1
}

own=0
while read -r tag || [ -n "$tag" ]; do
  released="${tag#v}"
  [[ "$released" =~ $loose ]] || continue
  [ "$released" = "$version" ] && { own=1; continue; }
  # A field past 9 digits would wrap bash arithmetic; refuse rather than guess.
  [[ "$released" =~ $strict ]] || [[ ! "$released" =~ [0-9]{10} ]] ||
    { echo "::error::cannot compare against $tag"; exit 1; }
  if ! above "$version" "$released"; then
    echo "::error::v$version is not above the released $tag"
    exit 1
  fi
done
[ "$own" = 1 ] || { echo "::error::v$version is not in the tag list; were tags fetched?"; exit 1; }
