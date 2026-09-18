"""Continuum — read-only cross-profile session project indexer.

Layering (architecture contract §2): M1 scanner -> M2 registry <- M3 evidence -> M4 cluster ->
M5 classify -> M6 model -> M7 service -> M8 desktop plugin.  M2 is the hub.

Nothing in this package writes to a Hermes source database. See scanner.open_readonly (INV-1).
"""
from __future__ import annotations

__all__ = [
    "config", "scanner", "registry", "evidence", "cluster", "classify", "model", "service",
]
__version__ = "0.1.0"
