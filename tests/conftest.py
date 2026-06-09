"""Load the hyphenated tool files (a hyphenated filename can't be imported normally)
and expose them as importable modules for the test suite."""
import importlib.util
import pathlib
import sys

_TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, _TOOLS / filename)
    mod = importlib.util.module_from_spec(spec)
    # Register BEFORE exec so dataclass/@dataclass (which resolves cls.__module__ via
    # sys.modules during class creation on Python 3.12+) can find the module.
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


fetch_attachments = _load("fetch_attachments", "fetch-attachments.py")
reminder_helper = _load("reminder_helper", "reminder-helper.py")
