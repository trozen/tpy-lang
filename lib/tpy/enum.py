# tpy: macro_module
"""Enum module for TurboPython.

Provides Enum, IntEnum base classes and auto() for member values.
The parser resolves base class names and auto() calls to ("enum", ...)
via normal import tracking. This module is loaded via CPython during
compilation (not compiled to C++).
"""


class Enum:
    """Base class for enumeration types."""
    pass


class IntEnum(Enum):
    """Base class for integer enumeration types."""
    pass


def auto():
    """Sentinel for auto-incrementing enum member values."""
    pass
