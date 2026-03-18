"""
TurboPython typing module.

Provides Protocol types matching Python's typing module.
All protocols (Sized, Sequence, etc.) are defined in lib/tpy/typing.py.
Dunder-to-C++ mappings are in DUNDER_CPP_TEMPLATES (modules/__init__.py).
"""

from tpyc.modules import BuiltinModule

NAME = "typing"


def init_module() -> BuiltinModule:
    """Initialize and return the typing module."""
    module = BuiltinModule(NAME)

    # Protocol is recognized by the parser as the base class for user-defined protocols
    # e.g., `class Measurable(Protocol):` generates a C++20 concept

    return module
