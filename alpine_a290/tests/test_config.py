"""Tests for the config seam: add-on option parsing (`cfg`/`_opt_flag`) and the
secret-redaction primitives (`redact` / `_RedactingFilter` / `_config_secrets`)."""
import logging

import config


def test_opt_flag_parses_values_and_defaults(monkeypatch):
    monkeypatch.setenv("A290_FLAG", "true")
    assert config._opt_flag("A290_FLAG", False) is True
    for v in ("false", "0", "off"):
        monkeypatch.setenv("A290_FLAG", v)
        assert config._opt_flag("A290_FLAG", True) is False
    for v in ("", "null", "  "):          # bashio can export these on an upgraded install
        monkeypatch.setenv("A290_FLAG", v)
        assert config._opt_flag("A290_FLAG", True) is True     # -> default
    monkeypatch.delenv("A290_FLAG", raising=False)
    assert config._opt_flag("A290_FLAG", True) is True         # unset -> default


def test_redact_masks_configured_secrets(monkeypatch):
    monkeypatch.setenv("A290_VIN", "VF1AAAABBBB12345")
    monkeypatch.setenv("A290_ACCOUNT_ID", "acct-9911")
    monkeypatch.setenv("A290_USERNAME", "me@example.com")
    monkeypatch.setenv("A290_PASSWORD", "hunter2")
    # an aiohttp-style error embedding the request URL (which carries the VIN + account id)
    err = RuntimeError("500, message='Server error', "
                       "url='https://api.example/accounts/acct-9911/vehicles/VF1AAAABBBB12345/charges'")
    out = config.redact(err)
    assert "VF1AAAABBBB12345" not in out and "acct-9911" not in out
    assert out.count("***") == 2 and "message='Server error'" in out   # non-secret text kept
    # empty/absent secrets never mask (would otherwise blank random text)
    for k in ("A290_VIN", "A290_ACCOUNT_ID", "A290_USERNAME", "A290_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert config.redact("nothing secret here") == "nothing secret here"


def test_redact_masks_auto_discovered_account_id(monkeypatch):
    # account_id left blank -> discovered at runtime; it still embeds in the Kamereon URL and
    # must be redacted even though it was never a configured (env) value.
    monkeypatch.delenv("A290_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(config, "_DISCOVERED_ACCOUNT_ID", "acct-discovered-42")
    out = config.redact("404 url='https://api/accounts/acct-discovered-42/vehicles/V/charges'")
    assert "acct-discovered-42" not in out and "***" in out


def test_redact_masks_supervisor_token(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "supervis-tok-abc")
    assert "supervis-tok-abc" not in config.redact("ws error with token supervis-tok-abc")


def test_redacting_filter_scrubs_log_records(monkeypatch):
    monkeypatch.setenv("A290_VIN", "VF1FILTERVIN")
    monkeypatch.setattr(config, "_DISCOVERED_ACCOUNT_ID", "acct-flt")
    rec = logging.LogRecord("x", logging.ERROR, __file__, 1,
                            "poll failed: %s",
                            ("url=/accounts/acct-flt/vehicles/VF1FILTERVIN/charges",), None)
    assert config._RedactingFilter().filter(rec) is True
    msg = rec.getMessage()
    assert "VF1FILTERVIN" not in msg and "acct-flt" not in msg
    assert msg.count("***") == 2 and rec.args == ()
