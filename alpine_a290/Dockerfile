# ── tempio builder ───────────────────────────────────────────────────────────
# The stock /usr/bin/tempio in the HA base image is built against golang.org/x/crypto
# 0.26.0 on a stale Go toolchain, so scanners flag it for a batch of CVEs (all in the
# unused crypto/ssh path, plus Go stdlib). Rebuild it on a current Go toolchain with
# x/crypto >= 0.52.0 and overwrite the base copy below. Verified: trivy reports 0 CVEs
# on the result. Tracking upstream fix: https://github.com/home-assistant/tempio/issues/132
# Remove this stage (and the COPY below) once a patched tempio ships in the base image.
FROM --platform=$BUILDPLATFORM golang:1.26-alpine AS tempio
# Pinned to an immutable commit for reproducible builds (tempio main @ 2026-06-30 —
# the revision verified to scan clean). Bump deliberately when upstream patches x/crypto.
ARG TEMPIO_REF=5420e16c8cca89903809c842b9925d5fe6778129
ARG TARGETARCH
ARG TARGETVARIANT
RUN apk add --no-cache git
WORKDIR /src
# Fetch the exact commit (git clone --branch can't take a bare SHA), then apply the
# x/crypto security override on top.
RUN git init -q . \
 && git remote add origin https://github.com/home-assistant/tempio \
 && git fetch -q --depth 1 origin "${TEMPIO_REF}" \
 && git checkout -q FETCH_HEAD \
 && go get golang.org/x/crypto@v0.52.0 \
 && go mod tidy
RUN CGO_ENABLED=0 GOOS=linux GOARCH="${TARGETARCH}" \
    GOARM="${TARGETVARIANT#v}" \
    go build -trimpath -ldflags="-s -w" -o /tempio .
# ──────────────────────────────────────────────────────────────────────────────

# Base image pinned here — the single source of truth. Since Supervisor 2026.04 no longer
# provides BUILD_FROM by default (and build.yaml's build_from is deprecated), the HA add-on
# standard is an explicit, pinned, multi-arch FROM. ghcr.io/home-assistant/base:3.24 is the
# current HA base (Alpine 3.24 / Python 3.14) and is multi-arch (amd64 + aarch64).
# Pinned by digest as well as tag so the base can't be repointed under a rebuild — the tag is
# kept for readability; the @sha256 is the immutable manifest-list (index) digest. Bump both
# together (resolve with `docker buildx imagetools inspect ghcr.io/home-assistant/base:<tag>`).
FROM ghcr.io/home-assistant/base:3.24@sha256:94ff231402a5e7ad2a82e261ad5fa4ffae7d7bb095c3febb2edbdf309c9b6aca

# Single source of truth for the version: the HA builder passes BUILD_VERSION from
# config.yaml's `version:`. Exposed to the app as A290_VERSION (see main.py).
ARG BUILD_VERSION="dev"
ENV A290_VERSION=${BUILD_VERSION}

# renault-api needs Python 3 + a few build deps for its crypto/aiohttp wheels.
RUN apk add --no-cache python3 py3-pip

# requirements.txt is a fully-resolved, hash-pinned lockfile (generated from requirements.in
# with `uv pip compile --generate-hashes --python-version 3.14 --python-platform linux`), so
# the whole dependency tree — not just the 3 top-level pins — is reproducible and integrity-
# checked. --require-hashes makes pip refuse any package whose artifact hash doesn't match.
COPY app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages --require-hashes -r /app/requirements.txt

# Shared a290/r5 core (renault-ha-core) — the code both add-ons run in common, held once and
# consumed here instead of hand-mirrored between the sibling repos (a290#56). Installed in its
# own step, AFTER the hash-locked requirements.txt and with --no-deps, so that lockfile stays
# the single hash-checked source of truth for the third-party tree; the core carries no new
# runtime deps of its own. Pinned by immutable commit SHA (the tag is kept for readability) —
# the same reproducibility discipline as the tempio rebuild above. Bump deliberately when a core
# change should ship; the build + container-verify gate confirms the new core boots clean.
# renault-ha-core v0.4.0
ARG CORE_REF=670a916903a5aca71fb857cb7722e7a80190a476
# git clones the pinned ref; py3-setuptools is the build backend for core's wheel. Both come from
# the apk repo (pinned by the base-image @sha256 digest above), and --no-build-isolation makes pip
# use that setuptools instead of fetching an unpinned one from PyPI — so the core install adds no
# unpinned build-time network dependency, preserving the --require-hashes reproducibility guarantee.
# --no-deps because core's runtime deps are already satisfied by the hash-locked tree. The whole
# toolchain is a virtual package purged in the same layer, so none of it ships in the final image —
# no size bloat, no extra CVE surface for the Trivy scan.
RUN apk add --no-cache --virtual .core-build git py3-setuptools \
 && pip3 install --no-cache-dir --break-system-packages --no-build-isolation --no-deps \
      "renault-ha-core @ git+https://github.com/MatthewHobbs/renault-ha-core@${CORE_REF}" \
 && apk del .core-build

COPY app/ /app/
# Bundle just the deployable dashboard YAMLs; their images load via CDN, so the ~5 MB of
# image assets stay in the repo and out of the container.
COPY dashboards/*.txt /app/dashboards/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Overwrite the base image's vulnerable /usr/bin/tempio with the patched build above.
COPY --from=tempio /tempio /usr/bin/tempio

# Supervisor monitors this; a deadlocked event loop can't answer /healthz -> unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8099/healthz || exit 1

CMD [ "/run.sh" ]
