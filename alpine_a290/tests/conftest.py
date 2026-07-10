"""Put the add-on's app/ dir on sys.path so tests can `import main` / `import deploy`."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))


@pytest.fixture(autouse=True)
def _isolate_module_globals():
    """Snapshot and restore main.py's mutable module-level singletons around every test.

    `_MQTT_CTX`, `_LATEST`, and `_DEBUG_STATE` are process-global dicts that several tests
    mutate in place; without this, a test that writes them leaks state into whatever runs next
    (order-dependent, and unsafe once tests run in parallel under pytest-xdist). Restoring here
    keeps each test isolated regardless of order."""
    import main
    dict_globals = ("_MQTT_CTX", "_LATEST", "_DEBUG_STATE")
    scalar_globals = ("_DISCOVERED_ACCOUNT_ID",)
    saved_dicts = {name: copy.deepcopy(getattr(main, name)) for name in dict_globals}
    saved_scalars = {name: getattr(main, name) for name in scalar_globals}
    yield
    for name, value in saved_dicts.items():
        target = getattr(main, name)
        target.clear()
        target.update(value)
    for name, value in saved_scalars.items():
        setattr(main, name, value)
