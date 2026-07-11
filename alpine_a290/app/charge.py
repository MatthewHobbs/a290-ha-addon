"""Charge-session reconciliation seam for the Alpine A290 add-on.

Two sources feed the "Last Charge" sensors and this module reconciles them:
  * update_charge_session() *infers* a session by watching live battery polls (works on every
    car, but its end time is when we first observed charging==false);
  * resolve_last_charge() reads Renault's own authoritative per-session record via the charges
    endpoint (when the car exposes it) and prefers it, falling back to the inferred value.

Depends only on the two leaves (config is not needed here; util for now_ts/iso/_num) plus the
CHARGES_ENDPOINT catalog constant — no cycle with main. main imports update_charge_session +
resolve_last_charge for the poll loop.
"""
import logging
from datetime import datetime, timedelta, timezone

from catalog import CHARGES_ENDPOINT
from renault_ha_core.util import _num, iso, now_ts

LOG = logging.getLogger("alpine_a290")

HOME_POWER_MAX_KW = 7.4   # above this average -> "Rapid/Public", else "Home"


def update_charge_session(state, battery, capacity_kwh, charging):
    soc = battery.batteryLevel
    power = _num(getattr(battery, "chargingInstantaneousPower", None)) or 0
    energy = _num(getattr(battery, "batteryAvailableEnergy", None))
    if energy is None and soc is not None:
        energy = round(soc / 100.0 * capacity_kwh, 2)

    if charging and not state.get("session_active"):
        LOG.info("Charge session START (soc=%s%%, power=%skW)", soc, power)
        state.update(session_active=True, start_ts=now_ts(), start_soc=soc,
                     start_energy=energy, start_power=power, pwr_accum=0.0, pwr_count=0)
    if charging and state.get("session_active") and power > 0:
        state["pwr_accum"] = state.get("pwr_accum", 0.0) + power
        state["pwr_count"] = state.get("pwr_count", 0) + 1
    if not charging and state.get("session_active"):
        start_ts = state.get("start_ts")
        dur = round((now_ts() - start_ts) / 60.0) if start_ts else None
        avg = round(state["pwr_accum"] / state["pwr_count"], 2) if state.get("pwr_count") else state.get("start_power")
        rec_pct = (soc - state["start_soc"]) if (soc is not None and state.get("start_soc") is not None) else None
        rec_kwh = round(energy - state["start_energy"], 2) if (energy is not None and state.get("start_energy") is not None) else None
        state["last_charge"] = {
            "last_charge_start": iso(start_ts),
            "last_charge_end": iso(now_ts()),
            "last_charge_start_soc": state.get("start_soc"),
            "last_charge_end_soc": soc,
            "last_charge_start_energy": round(state["start_energy"], 2) if state.get("start_energy") is not None else None,
            "last_charge_end_energy": round(energy, 2) if energy is not None else None,
            "last_charge_recovered_pct": rec_pct,
            "last_charge_recovered_kwh": rec_kwh,
            "last_charge_duration_min": dur,
            "last_charge_average_power": avg,
            "last_charge_type": "Rapid/Public" if (avg or 0) > HOME_POWER_MAX_KW else "Home",
        }
        LOG.info("Charge session END (dur=%smin, +%s%%, +%skWh, avg=%skW)", dur, rec_pct, rec_kwh, avg)
        state["session_active"] = False
        state["charges_dirty"] = True   # a session just ended -> refetch authoritative charges next poll
    return state.get("last_charge", {})


# --- Authoritative Last Charge via the charges endpoint -------------------------------
# update_charge_session() above *infers* the last session by watching live battery polls.
# When the car exposes the charges endpoint we instead read Renault's own per-session record
# (get_charges -> raw_data["charges"]) and prefer it. The inferred value stays as the fallback
# for cars/sessions the endpoint doesn't (yet) cover; newest-end wins so a just-finished
# session shows live immediately, then gets replaced by the authoritative record once posted.
CHARGES_LOOKBACK_DAYS = 14
CHARGES_REFRESH_SEC = 1800   # between charges reads; an ended session forces an immediate refetch
# Renault's authoritative chargeEndDate is the *actual* stop time; the inferred fallback's end
# is when this add-on first *observed* charging==false (up to a poll cycle + posting delay
# later). Treat an endpoint session ending within this window of the inferred one as the SAME
# session, so the authoritative record still wins; only a live session ending materially later
# (a fresh charge the history hasn't posted yet) keeps the inferred values.
CHARGE_MATCH_TOLERANCE_SEC = 3600


def _epoch(s):
    """ISO-8601 string -> epoch seconds, or None if unparseable. Tolerates a trailing 'Z'."""
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _parse_charge_session(charges, capacity_kwh):
    """Most recent *completed* session from a get_charges() payload, in the same
    last_charge_* shape update_charge_session() produces ({} when none usable). Duration is
    derived from the start/end timestamps (sidesteps the per-model seconds-vs-minutes quirk in
    chargeDuration); energy/power fall back to capacity-scaled SoC when the API omits kWh."""
    done = [c for c in (charges or []) if isinstance(c, dict) and c.get("chargeEndDate")]
    if not done:
        return {}
    c = max(done, key=lambda x: _epoch(x.get("chargeEndDate")) or 0.0)
    start_soc = _num(c.get("chargeStartBatteryLevel"))
    end_soc = _num(c.get("chargeEndBatteryLevel"))
    rec_pct = _num(c.get("chargeBatteryLevelRecovered"))
    if rec_pct is None and None not in (start_soc, end_soc):
        rec_pct = round(end_soc - start_soc, 2)
    rec_kwh = _num(c.get("chargeEnergyRecovered"))
    if rec_kwh is None and rec_pct is not None:
        rec_kwh = round(rec_pct / 100.0 * capacity_kwh, 2)
    s_ep, e_ep = _epoch(c.get("chargeStartDate")), _epoch(c.get("chargeEndDate"))
    dur = round((e_ep - s_ep) / 60.0) if (s_ep is not None and e_ep is not None and e_ep >= s_ep) else None
    avg = round(rec_kwh / (dur / 60.0), 2) if (rec_kwh and dur) else _num(c.get("chargeStartInstantaneousPower"))
    start_energy = round(start_soc / 100.0 * capacity_kwh, 2) if start_soc is not None else None
    end_energy = round(end_soc / 100.0 * capacity_kwh, 2) if end_soc is not None else None
    return {
        "last_charge_start": c.get("chargeStartDate"),
        "last_charge_end": c.get("chargeEndDate"),
        "last_charge_start_soc": start_soc,
        "last_charge_end_soc": end_soc,
        "last_charge_start_energy": start_energy,
        "last_charge_end_energy": end_energy,
        "last_charge_recovered_pct": rec_pct,
        "last_charge_recovered_kwh": round(rec_kwh, 2) if rec_kwh is not None else None,
        "last_charge_duration_min": dur,
        "last_charge_average_power": avg,
        "last_charge_type": "Rapid/Public" if (avg or 0) > HOME_POWER_MAX_KW else "Home",
    }


def _prefer_real_charge(real, live):
    """True when the authoritative endpoint session `real` should replace the inferred `live`.
    The endpoint wins unless the inferred session ends *materially* later than the endpoint's
    (more than CHARGE_MATCH_TOLERANCE_SEC) — i.e. a just-finished session the history hasn't
    posted yet. An unparseable endpoint date never displaces a live session."""
    if not real:
        return False
    if not live:
        return True
    re_, le = _epoch(real.get("last_charge_end")), _epoch(live.get("last_charge_end"))
    if re_ is None:
        return False
    if le is None:
        return True
    return le - re_ <= CHARGE_MATCH_TOLERANCE_SEC


def _due_for_charges(state):
    """Throttle charges reads: once per CHARGES_REFRESH_SEC, or immediately after a session end."""
    if state.get("charges_dirty"):
        return True
    last = state.get("charges_last_fetch")
    return last is None or (now_ts() - last) >= CHARGES_REFRESH_SEC


async def fetch_real_last_charge(vehicle, capacity_kwh):
    """Read recent charge sessions and return the latest completed one in last_charge_* shape."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=CHARGES_LOOKBACK_DAYS)
    end = now + timedelta(days=1)   # get_charges params are day-granular (%Y%m%d); query through
                                    # tomorrow so a session that ended *today* is in-window
    res = await vehicle.get_charges(start, end)
    raw = getattr(res, "raw_data", None) or {}
    return _parse_charge_session(raw.get("charges"), capacity_kwh)


async def resolve_last_charge(vehicle, state, supported_eps, capacity_kwh, live_lc):
    """Pick the Last Charge to publish: the authoritative charges-endpoint session when it's
    at least as recent as the inferred one, else the inferred session. Caches the endpoint
    result in state so it's only re-read on the throttle/after a session ends."""
    if CHARGES_ENDPOINT in supported_eps and _due_for_charges(state):
        state["charges_dirty"] = False
        state["charges_last_fetch"] = now_ts()
        try:
            real = await fetch_real_last_charge(vehicle, capacity_kwh)
            if real:
                state["real_last_charge"] = real
        except Exception as err:  # noqa: BLE001
            LOG.warning("charges endpoint unavailable: %s", err)
    real_lc = state.get("real_last_charge", {})
    return real_lc if _prefer_real_charge(real_lc, live_lc) else live_lc
