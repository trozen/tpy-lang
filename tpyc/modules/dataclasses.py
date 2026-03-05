"""
TurboPython dataclasses module.

Provides the @dataclass decorator. Actual semantics (init synthesis, etc.)
are handled by parser and sema -- this module exists so that
`from dataclasses import dataclass` resolves without error.
"""

from tpyc.modules import BuiltinModule

NAME = "dataclasses"


def init_module() -> BuiltinModule:
    """Initialize and return the dataclasses module.

    Intentionally empty -- @dataclass semantics are handled by the parser
    and sema. This module exists solely so that `from dataclasses import
    dataclass` resolves. Future: `field()` support would be registered here.
    """
    module = BuiltinModule(NAME)
    return module
