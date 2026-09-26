# ADR 0002: Test the add-on under the current stable Supervisor and Core

- **Status:** Proposed (2026-09-26). Accepted when the owner merges it with rows 1 to 4 and 6 Done.
- **Context:** the global rule "Home Assistant: test against current stable" asks for a real stable Supervisor and Core, at both ends of the supported range. Owner decisions of 2026-09-26: (1) adopt polygonal-zones' Supervisor pilot here first, with r5 mirroring it once this is green; (2) pin the AppArmor ABI in this add-on's profile.
- **Pattern:** [polygonal-zones ADR 0001](https://github.com/MatthewHobbs/Homeassistant-polygonal-zones-addon/blob/main/docs/adr/0001-test-under-stable-supervisor.md), including its 2026-09-26 amendment, and its PR #52 and release #54 (0.4.2). Its row 13 names this repo, but carries no authority here, so this ADR does.
- **North star:** every release has been installed, configured and run under the current stable Supervisor, with both current stable Core and the declared minimum Core, on amd64 and aarch64, with AppArmor enforced, before it ships.

## Decision

Run the add-on in CI under a real stable-channel Supervisor in the official add-on devcontainer, as polygonal-zones does. Keep its three solved traps, adapted to this add-on as below.

| # | Step | Owner | Status | Evidence |
| --- | --- | --- | --- | --- |
| 1 | `scripts/supervisor-pilot.sh` and `.github/workflows/supervisor.yml`: the devcontainer (pinned by digest) with `SUPERVISOR_CHANNEL=stable`. It installs the add-on from `apps/local`, sets stub options through the Supervisor API and starts it. Jobs run on `ubuntu-latest` (amd64) and `ubuntu-24.04-arm` (aarch64). Nightly, on demand, and on PRs that touch the pilot or the add-on's runtime files | a290 | Done | Local runs green on Apple Silicon (arm64, Supervisor 2026.09.2): stable Core 2026.9.3 in 94 s, floor 2026.8.1 in 92 s. CI runs pending; Runner, amd64 and aarch64 × Core stable and minimum: run 36239779396 (#160, Run B) green on all four; must-fail runs 36239099808 (Run C, PILOT_BREAK=start: failed at install) and 36239101111 (Run D, PILOT_BREAK=apparmor: failed at probe, 'docker-default, not its own profile') |
| 2 | Each leg checks the running Supervisor, and the image it runs, against `stable.json`. Core must match stable, or the `homeassistant:` minimum read from `config.yaml` (as `ui-tests.yaml` reads it). Any mismatch fails | a290 | Done | Locally, a wrong expected Core or Supervisor fails the check. CI pending; Runner, amd64 and aarch64 × Core stable and minimum: run 36239779396 (#160, Run B) green on all four; must-fail runs 36239099808 (Run C, PILOT_BREAK=start: failed at install) and 36239101111 (Run D, PILOT_BREAK=apparmor: failed at probe, 'docker-default, not its own profile') |
| 3 | The image that would ship: the checkout is built as `release.yaml` builds it, served from a registry on the devcontainer's loopback (the Supervisor always pulls), and the running container's provenance label (commit plus a per-run nonce) must match | a290 | Done | Locally: expected and observed labels matched. CI pending; Runner, amd64 and aarch64 × Core stable and minimum: run 36239779396 (#160, Run B) green on all four; must-fail runs 36239099808 (Run C, PILOT_BREAK=start: failed at install) and 36239101111 (Run D, PILOT_BREAK=apparmor: failed at probe, 'docker-default, not its own profile') |
| 4 | Probes: `/healthz` returns `ok`; the image's `HEALTHCHECK` reports healthy; retained discovery configs under `homeassistant/+/alpine_a290/+/config` all name the `alpine_a290` device; retained `alpine_a290/availability` is `online`; the add-on tried Renault and the egress rule refused it (row 7); AppArmor (row 6) | a290 | Done | Locally: 47 configs (38 sensor, 8 binary_sensor, 1 device_tracker) and 12 refused connections. A wrong node fails; `PILOT_BREAK=start` fails at install. CI pending; Runner, amd64 and aarch64 × Core stable and minimum: run 36239779396 (#160, Run B) green on all four; must-fail runs 36239099808 (Run C, PILOT_BREAK=start: failed at install) and 36239101111 (Run D, PILOT_BREAK=apparmor: failed at probe, 'docker-default, not its own profile') |
| 5 | Promotion to a required check, and a job `release.yaml` runs before publishing, after about two weeks if every failure had a known cause that was not flakiness. Owner's call, recorded here | owner | Open | |
| 6 | AppArmor enforced, not just loaded: the kernel has `local_alpine_a290 (enforce)`, the container was started with it, every process in it carries it, and the kernel logged no denial for it. Enforced only on the runner: Docker Desktop's kernel has no AppArmor, so locally the check fails unless `PILOT_APPARMOR_CHECK=skip`, which the script refuses under Actions. **Establish whether this profile is denied without the ABI pin (row 8)** | a290 | Done | Locally, the check fails on a kernel without AppArmor, and the skip switch is refused with `GITHUB_ACTIONS` set. CI pending; Runner, amd64 and aarch64 × Core stable and minimum: run 36239779396 (#160, Run B) green on all four; must-fail runs 36239099808 (Run C, PILOT_BREAK=start: failed at install) and 36239101111 (Run D, PILOT_BREAK=apparmor: failed at probe, 'docker-default, not its own profile') |
| 7 | Renault never reached. `--add-host` (CLAUDE.md's boot test) can't be given to a container the Supervisor creates. Instead, an iptables `DOCKER-USER` rule refuses everything from the add-on range `172.30.33.0/24` to anywhere off the hassio network `172.30.32.0/23` (IPv6 likewise, if hassio has it). The probe requires the add-on's address to be in that range and the rule to have refused something | a290 | Open | Locally: 0 refused before start, 12 after. The add-on logged `Cannot connect to host accounts.eu1.gigya.com:443 … Connect call failed`. CI pending |
| 8 | Pin the policy ABI: `abi <abi/3.0>,` as the profile's first line (owner decision 2). Released as 1.28.3 | a290 | Done | Compiles under parsers 3.0.8, 3.1.7, 4.0.1, 4.1.0 (the devcontainer's) and 5.0.0~alpha1; each rejects `abi/9.9`. Without it, the runner (kernel 6.17, parser 4.1.0) denied the add-on on all four legs: `apparmor="DENIED" … info="failed protocol match" profile="local_alpine_a290" comm="s6-ipcserver-so" family="unix"` (run 36238665001, #160 Run A). With it: run 36239779396 (Run B) green on all four legs, every process under `local_alpine_a290 (enforce)`, no denial |
| 9 | The broker is the official Mosquitto add-on, with a pilot login for the probe | a290 | Open | Locally: installed, started and registered `mqtt` in 3 to 4 s |
| 10 | Mirror into r5-ha-addon once rows 1 to 4, 6 and 8 are Done here. Needs a session there | r5 | Open | rows 1 to 4, 6 and 8 Done |

Status is one of **Open**, **Done**, **Blocked**, **Dropped**. A **Done** row carries Evidence.

## Context

Until now, a290's CI built the image and never booted it. The container boot in CLAUDE.md is manual and standalone: no Supervisor, no options from the Supervisor, no service registry, no AppArmor. A profile change has already shipped that CI could not catch: `apparmor.txt` records an earlier profile that denied `/init`, so the add-on could not start after updating. polygonal-zones' pilot then found that the GitHub runners, on kernel 6.17 with the devcontainer's parser 4.1.0, enforce AppArmor. Its unpinned profile was denied a unix socket there (`apparmor="DENIED" operation="create" class="net" info="failed protocol match" … family="unix"`); adding `unix,` changed nothing, and `abi <abi/3.0>,` fixed it.

This profile differs from polygonal's. It already grants `unix,`, and it grants `network` only for inet/inet6 tcp and udp and netlink raw, where polygonal had a bare `network,`. So polygonal's result does not settle whether this profile fails without the pin. Row 6 settles it on the runner.

## Alternatives

polygonal-zones ADR 0001 decided among four options: the official devcontainer (chosen), the Supervisor container run directly, HA OS in QEMU, and cheap wins only. It is the pattern, and its table stands for this ADR. The two choices made fresh here:

| Choice | Option | Cost | Risk |
| --- | --- | --- | --- |
| Broker | **Mosquitto add-on** (chosen) | About 10 s per leg to install; a pilot login | Tracks the store's current Mosquitto, which is not pinned, so a broker release can change a run. It is also what users run |
| Broker | Mosquitto service container | Cheaper; pinnable | `run.sh` takes the broker from the Supervisor's service registry (`bashio::services mqtt`), which only an add-on declaring `mqtt:provide` fills. The add-on would exit with `Missing required setting: MQTT_HOST`, so the path users take would go untested |
| No Renault traffic | **iptables refusal of the add-on range** (chosen) | Two rules | Depends on the Supervisor's address plan (`const.py`); the probe checks its precondition. The DNS lookup still leaves the runner; no connection does |
| No Renault traffic | Hosts entries in the Supervisor's DNS plugin | Similar | The Supervisor owns and rewrites that file; covers only the hosts listed, like `--add-host` |

## Consequences

**Accepted:** four privileged jobs of about two minutes each (warm) per run, and two more digests for Renovate (grouped as "Supervisor pilot images"). The pilot is not required until row 5, so a release can still ship without it. It enforces AppArmor on a newer parser and kernel than HA OS ships, so it can fail on breakage HA OS users have not hit yet; that is the point.

**Watch:** local add-on discovery is known to be fragile (supervisor#3976). The floor-Core seed relies on the Supervisor's internal `homeassistant.json`, and the version check is what catches a change there. The Mosquitto add-on is unpinned.

## Verification (2026-09-26, local, Apple Silicon)

- Stable leg: Supervisor 2026.09.2, Core 2026.9.3, `machine=qemuarm-64`; every phase passed except AppArmor, which was skipped (no AppArmor in that kernel).
- Floor leg: Core 2026.8.1 seeded and observed (image `qemuarm-64-homeassistant:2026.8.1`); every phase passed, same skip.
- Negative controls: wrong expected Core, wrong expected Supervisor, a discovery node that does not exist, the AppArmor check on a kernel without AppArmor, the skip switch under `GITHUB_ACTIONS`, the add-on stopped (the Supervisor removes the container, so the probe failed on "no container"), and `PILOT_BREAK=start` (failed at install: never reported started). Each failed.
- **Not established:** anything about AppArmor enforcement, including row 6's question; the aarch64 and amd64 runners; runtime and flakiness on CI; `PILOT_BREAK=apparmor`, which is meaningful only where AppArmor is enforced.

## References

- polygonal-zones ADR 0001 and its amendment; its PR #52 (pilot) and #54 (ABI pin, 0.4.2)
- Stable channel: https://version.home-assistant.io/stable.json
- Supervisor source: `supervisor/const.py` (hassio network and add-on range), `supervisor/docker/app.py` (`app_` container names, `apparmor=<slug>`), `supervisor/utils/apparmor.py` (profile renamed to the slug)
