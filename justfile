# a290-ha-addon governance recipes (the cross-repo `just` convention).
#
# `just ci` mirrors the static gates in .github/workflows/ci.yaml, including the same
# dependency install, so a local pass means the same thing CI does.
# NOT covered locally, and remaining remote-only gates: Hadolint, the HA add-on linter,
# Bandit / pip-audit / Trivy, and the multi-arch image build. A runtime change still needs
# the Tier-0 container boot - see CLAUDE.md.

# Local CI gate - the same commands remote CI runs, for the checks it covers.
# `parity` itself is not in here: it needs the r5 twin (a sibling checkout or a clone), so
# only its self-test is. The Parity job in ci.yaml runs the full comparison.
ci: lint pii ha-min parity-self-test test

# Proves the parity check can fail on each kind of drift; needs nothing outside this repo.
parity-self-test:
    python3 scripts/parity_check.py --self-test

# Same command as the Parity job: diff this add-on against r5-ha-addon through the committed
# map and expected list (ADR 0001). PARITY_TWIN=<path> must be a git checkout of r5: its
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
    uv pip install --python .venv --quiet pytest pytest-cov
    CORE_REF="$(sed -n 's/^ARG CORE_REF=//p' alpine_a290/Dockerfile)"
    echo "renault-mqtt pinned to ${CORE_REF}"
    uv pip install --python .venv --quiet --no-deps \
      "renault-mqtt @ git+https://github.com/MatthewHobbs/renault-mqtt@${CORE_REF}"

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
    shellcheck alpine_a290/run.sh
    ruff check alpine_a290/app alpine_a290/tests scripts ui-tests

test: venv
    .venv/bin/python -m pytest alpine_a290/tests -q --cov=alpine_a290/app --cov-report=term-missing --cov-fail-under=95
