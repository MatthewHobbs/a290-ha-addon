"""a290-side wiring contract for the shared config seam.

The generic config behaviour (cfg / _opt_flag / redact / _RedactingFilter) is tested in the
renault-mqtt package. What stays a290's responsibility is the *wiring*: that this add-on
injects its own env-var prefix (A290_) into the shared redaction net, so real A290_ secrets are
actually masked. Importing main (conftest does this for every test) runs that injection.
"""
import catalog
import main  # noqa: F401 — imported for its side effect: sets config.ENV_PREFIX from catalog
from renault_mqtt import config


def test_catalog_defines_the_a290_env_prefix():
    assert catalog.ENV_PREFIX == "A290_"


def test_main_injects_the_env_prefix_into_the_core_redaction_net():
    # main.py sets `config.ENV_PREFIX = catalog.ENV_PREFIX` at import time.
    assert config.ENV_PREFIX == "A290_"


def test_redaction_net_masks_real_a290_secrets_end_to_end(monkeypatch):
    # Proof the wiring reaches the shared net: a configured A290_VIN embedded in an API error
    # URL is masked by the core's redact() because ENV_PREFIX resolves _config_secrets to the
    # A290_-prefixed option names.
    monkeypatch.setenv("A290_VIN", "VF1WIRINGVIN0001")
    monkeypatch.setenv("A290_ACCOUNT_ID", "acct-wire-7")
    out = config.redact("500 url='https://api/accounts/acct-wire-7/vehicles/VF1WIRINGVIN0001/charges'")
    assert "VF1WIRINGVIN0001" not in out and "acct-wire-7" not in out
    assert out.count("***") == 2
