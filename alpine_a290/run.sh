#!/usr/bin/with-contenv bashio
# shellcheck shell=bash disable=SC2155
# ---------------------------------------------------------------------------
# Alpine A290 add-on entrypoint.
# Reads the add-on options + the MQTT broker details (auto-discovered from the
# Mosquitto add-on) and hands them to the Python poller as environment vars.
# ---------------------------------------------------------------------------
set -e

bashio::log.info "Starting Alpine A290 add-on..."

# MQTT broker — taken from the Mosquitto add-on via the Supervisor services API.
if bashio::services.available "mqtt"; then
    export MQTT_HOST="$(bashio::services mqtt 'host')"
    export MQTT_PORT="$(bashio::services mqtt 'port')"
    export MQTT_USER="$(bashio::services mqtt 'username')"
    export MQTT_PASS="$(bashio::services mqtt 'password')"
    bashio::log.info "Using MQTT broker at ${MQTT_HOST}:${MQTT_PORT}"
else
    bashio::log.warning "No MQTT service found — install/enable the Mosquitto broker add-on."
fi

# Add-on options (the dedicated config page).
export A290_USERNAME="$(bashio::config 'username')"
export A290_PASSWORD="$(bashio::config 'password')"
export A290_ACCOUNT_ID="$(bashio::config 'account_id')"
export A290_VIN="$(bashio::config 'vin')"
export A290_LOCALE="$(bashio::config 'locale')"
export A290_POLL_INTERVAL="$(bashio::config 'poll_interval')"
export A290_BATTERY_CAPACITY_KWH="$(bashio::config 'battery_capacity_kwh')"
export A290_STALE_HOURS="$(bashio::config 'stale_hours')"
export A290_GPS_PRECISION="$(bashio::config 'gps_precision')"
export A290_PRECONDITION_TEMPERATURE="$(bashio::config 'precondition_temperature')"
export A290_LOG_LEVEL="$(bashio::config 'log_level')"
export A290_DEBUG_DUMP="$(bashio::config 'debug_dump')"

# Dashboard auto-deploy (talks to the HA core API; SUPERVISOR_TOKEN is injected
# by the Supervisor when homeassistant_api: true).
export A290_DEPLOY_DASHBOARD="$(bashio::config 'deploy_dashboard')"
export A290_DASHBOARD_URL_PATH="$(bashio::config 'dashboard_url_path')"
export A290_REDEPLOY_DASHBOARD="$(bashio::config 'redeploy_dashboard')"

# Optional EV-charger entities for the dashboard's "Smart Charging" card (any charger
# integration, e.g. Octopus Intelligent). Blank ones are skipped; all blank => no card.
export A290_CHARGER_SMART_CHARGE="$(bashio::config 'charger_smart_charge')"
export A290_CHARGER_BUMP_CHARGE="$(bashio::config 'charger_bump_charge')"
export A290_CHARGER_TARGET_SOC="$(bashio::config 'charger_target_soc')"
export A290_CHARGER_TARGET_TIME="$(bashio::config 'charger_target_time')"
export A290_CHARGER_DISPATCHING="$(bashio::config 'charger_dispatching')"

exec python3 -u /app/main.py
