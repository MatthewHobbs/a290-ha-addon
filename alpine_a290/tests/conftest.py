"""Put the add-on's app/ dir on sys.path so tests can `import main` / `import deploy`."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture(autouse=True)
def _isolate_module_globals():
    """Snapshot and restore main.py's mutable module-level singletons around every test.

    `_LATEST` (main), `_MQTT_CTX` (mqtt) and `_DEBUG_STATE` (debug) are process-global dicts
    that several tests mutate in place; without this, a test that writes them leaks state into
    whatever runs next (order-dependent, and unsafe once tests run in parallel under
    pytest-xdist). Restoring here keeps each test isolated regardless of order.
    `config._DISCOVERED_ACCOUNT_ID` is the same concern for the redaction seam (set by
    resolve_account, read by redact)."""
    import main
    from renault_mqtt import config, debug, mqtt
    dict_globals = ((main, "_LATEST"), (mqtt, "_MQTT_CTX"), (debug, "_DEBUG_STATE"))
    scalar_globals = ((config, "_DISCOVERED_ACCOUNT_ID"),)
    saved_dicts = {(mod, name): copy.deepcopy(getattr(mod, name)) for mod, name in dict_globals}
    saved_scalars = {(mod, name): getattr(mod, name) for mod, name in scalar_globals}
    yield
    for (mod, name), value in saved_dicts.items():
        target = getattr(mod, name)
        target.clear()
        target.update(value)
    for (mod, name), value in saved_scalars.items():
        setattr(mod, name, value)
