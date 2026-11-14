"""Compatibility package for shared backend contracts.

The original project kept these contracts in ``contracts.py``.  Re-export that
module here so the research sub-contract can use the requested package path
without changing existing imports.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_legacy_path = Path(__file__).resolve().parent.parent / "contracts.py"
_spec = importlib.util.spec_from_file_location("_mablop_legacy_contracts", _legacy_path)
if _spec is None or _spec.loader is None:  # pragma: no cover
    raise ImportError("shared contracts are unavailable")
_legacy = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _legacy
_spec.loader.exec_module(_legacy)

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)

__all__ = [name for name in globals() if not name.startswith("_")]
