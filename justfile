# a290-ha-addon governance recipes (the cross-repo `just` convention).
#
# `just ci` mirrors the static gates in .github/workflows/ci.yaml, including the same
# dependency install, so a local pass means the same thing CI does.
# NOT covered locally, and remaining remote-only gates: Hadolint, the HA add-on linter,
# Bandit / pip-audit / Trivy, and the multi-arch image build. A runtime change still needs
# the Tier-0 container boot - see CONTRIBUTING.md.

# Local CI gate - the same commands remote CI runs, for the checks it covers.
# `parity` itself is not in here: it needs the r5 twin (a sibling checkout or a clone), so
# only its self-test is. The Parity job in ci.yaml runs the full comparison.
ci: lint comments adr pii ha-min parity-self-test docs-sync-self-test test

# Proves the release guard, the release script and the version-order check can fail, and that the
# script writes exactly what the guard accepts. The Docs sync job in ci.yaml runs the same three.
docs-sync-self-test:
    python3 scripts/docs_sync_check.py --self-test
    python3 scripts/prepare_release.py --self-test
    python3 scripts/adr_evidence_check.py --self-test
    scripts/test-check-version-order.sh

# Cut a release: turn `## Unreleased` into `## <version>` and move config.yaml's version to match,
# and nothing else. Commit the result as its own PR, titled `chore(release): <version>`;
# merging it publishes nothing; `just tag` does. `--dry-run` only prints.
release version *flags:
    python3 scripts/prepare_release.py {{version}} {{flags}}

# Sign and push the tag for a merged release; release.yaml then publishes it. Tags the
# release PR's merge commit on origin/main, not whatever main is now.
tag version:
    #!/usr/bin/env bash
    set -euo pipefail
    git fetch --quiet --tags origin main
    sha="$(git log origin/main -1 --format=%H --grep="^chore(release): {{version}} ")"
    [[ -n "$sha" ]] || { echo "no merged chore(release): {{version}} commit on origin/main"; exit 1; }
    [[ "$(git show "$sha:alpine_a290/config.yaml" | awk -F'"' '/^version:/{print $2}')" == "{{version}}" ]] ||
        { echo "config.yaml at $sha is not version {{version}}"; exit 1; }
    ! git rev-parse -q --verify "refs/tags/v{{version}}" >/dev/null ||
        { echo "v{{version}} already exists locally"; exit 1; }
    rc=0
    git ls-remote --exit-code --tags origin "refs/tags/v{{version}}" >/dev/null 2>&1 || rc=$?
    case "$rc" in
      0) echo "v{{version}} already exists on origin"; exit 1 ;;
      2) : ;;
      *) echo "could not check origin for v{{version}} (git ls-remote exit $rc)"; exit 1 ;;
    esac

    # Every check runs before the irreversible tag: version order, on main, and the
    # workflows that validated this exact merge commit. A failure costs nothing yet.
    { git tag -l 'v*'; echo "v{{version}}"; } | scripts/check-version-order.sh "{{version}}"
    git merge-base --is-ancestor "$sha" origin/main ||
        { echo "$sha is not on origin/main"; exit 1; }
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
      # A workflow registers its run seconds after the push, so an empty pending set is
      # not proof every gate ran. Settle, then require the run count to be unchanged.
      if (( count > 0 && pending == 0 )); then
        sleep "${TAG_SETTLE_SECONDS:-60}"
        settled="$(gh run list --commit "$sha" --limit 50 --json workflowName)"
        if [[ "$(jq 'length' <<<"$settled")" == "$count" ]]; then break; fi
        continue
      fi
      sleep 20
    done

    git tag -s "v{{version}}" -m "Alpine A290 {{version}}" "$sha"
    git push origin "v{{version}}"

# Proves the parity check can fail on each kind of drift; needs nothing outside this repo.
parity-self-test:
    python3 scripts/parity_check.py --self-test

# Same command as the Parity job: diff this add-on against r5-ha-addon through the committed
# map and expected list. PARITY_TWIN=<path> must be a git checkout of r5: its
# tracked files (content as in its working tree) and tracked modes are compared; anything else
# is refused rather than silently walked. Unset, it clones r5's main (network).
parity: parity-self-test
    #!/usr/bin/env bash
    set -euo pipefail
    twin="${PARITY_TWIN:-}"
    if [ -z "$twin" ]; then
      tmp="$(mktemp -d)"
      trap 'rm -rf "$tmp"' EXIT
      twin="$tmp/r5-ha-addon"
      git clone --quiet --depth 1 https://github.com/MatthewHobbs/r5-ha-addon "$twin"
    fi
    python3 scripts/parity_check.py --twin "$twin"

# Test env built exactly as CI builds it. requirements.txt is hash-pinned, so it installs
# alone; the core is then added --no-deps at the SHA the Dockerfile pins, so local tests
# run against the exact core the image ships.
venv:
    #!/usr/bin/env bash
    set -euo pipefail
    uv venv --python 3.14 --quiet --allow-existing .venv
    uv pip install --python .venv --quiet -r alpine_a290/app/requirements.txt
    uv pip install --python .venv --quiet pytest==9.1.1 pytest-cov==7.1.0
    CORE_REF="$(sed -n 's/^ARG CORE_REF=//p' alpine_a290/Dockerfile)"
    echo "renault-mqtt pinned to ${CORE_REF}"
    uv pip install --python .venv --quiet --no-deps \
      "renault-mqtt @ git+https://github.com/MatthewHobbs/renault-mqtt@${CORE_REF}"

# Scope matches the sibling pair: the ui-tests harness keeps its measured findings.
# Code comment blocks stay under 50 words.
comments:
    python3 scripts/check_comment_length.py alpine_a290 scripts .github

# The decision record cites files and symbols; fail on one the tree no longer has.
adr:
    python3 scripts/adr_evidence_check.py

# Same command as the Security job's PII step, so a leak is caught before it is published.
pii:
    python3 scripts/pii_check.py --self-test
    python3 scripts/pii_check.py

# Same command as the Lint job's minimum-HA step (reads current stable live).
ha-min:
    python3 scripts/ha_minimum_check.py --self-test
    python3 scripts/ha_minimum_check.py alpine_a290/config.yaml

lint:
    yamllint -c .yamllint alpine_a290 repository.yaml
    shellcheck alpine_a290/run.sh scripts/supervisor-pilot.sh scripts/check-version-order.sh scripts/test-check-version-order.sh
    # The ruff ruff.toml requires (required-version), so a local pass means what CI's pinned ruff means.
    uvx "ruff$(sed -n 's/^required-version = "\(.*\)"$/\1/p' ruff.toml)" check alpine_a290/app alpine_a290/tests scripts ui-tests

test: venv
    .venv/bin/python -m pytest alpine_a290/tests -q --cov=alpine_a290/app --cov-report=term-missing
