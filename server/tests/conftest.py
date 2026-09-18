"""Shared test bootstrap: put the build root on sys.path so ``continuum`` and ``fixtures`` import."""
from __future__ import annotations

import os
import sys

BUILD_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BUILD_ROOT not in sys.path:
    sys.path.insert(0, BUILD_ROOT)

ROSTER = ["aetherean", "azaraki", "halakukhan", "kodekoot", "kurimasu", "lugia", "shayba",
          "sheikh-al-jabr"]


def real_hermes_home() -> str:
    return os.environ.get("CONTINUUM_TEST_HERMES_HOME", os.path.expanduser("~/.hermes"))
