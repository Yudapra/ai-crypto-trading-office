"""Test package. Adds source roots to sys.path so tests run with `python -m unittest` or pytest."""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "backend"), os.path.join(_ROOT, "trading-engine")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
