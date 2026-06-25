"""Put the add-on's app/ dir on sys.path so tests can `import main` / `import deploy`."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
