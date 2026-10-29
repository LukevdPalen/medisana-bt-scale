"""Test helpers for the Medisana BT Scale integration.

`parser.py` is intentionally dependency-free (no bleak, no Home Assistant)
so it can be unit tested on its own - but importing it via the normal
dotted path (`custom_components.medisana_bt_scale.parser`) would still first
execute `custom_components/medisana_bt_scale/__init__.py`, which does import
Home Assistant. Since a plain test environment doesn't have `homeassistant`
installed, pure modules are instead loaded directly by file path,
bypassing the package's __init__.py entirely.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

COMPONENT_DIR = (
    Path(__file__).resolve().parents[1] / "custom_components" / "medisana_bt_scale"
)


def load_component_module(name: str) -> ModuleType:
    """Load a single integration module from disk, standalone."""
    spec = importlib.util.spec_from_file_location(
        f"medisana_bt_scale_{name}", COMPONENT_DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclasses.dataclass() looks up
    # sys.modules[cls.__module__] while resolving type hints, which fails
    # if the module isn't registered yet.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
