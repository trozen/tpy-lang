# tpy: ext_module
# Docstrings on the exposed surface cross to the host as __doc__: the module
# (m_doc), free functions and methods (ml_doc), the class (Py_tp_doc), a
# @property (PyGetSetDef.doc) and a user exception type. An undocumented
# callable keeps __doc__ None, exactly as CPython does. A DUNDER's docstring is
# the exception: it reaches CPython as a type slot, which has no doc field, so
# the text is dropped (with a warning) and CPython substitutes its own -- see
# ext_checks.py, which pins that ext-only because it diverges from the source.
# The crossing text is DEDENTED like CPython 3.13+ does at compile time, so an
# indented docstring's __doc__ is pinned ext-only too: the parity run's answer
# depends on the host interpreter's version, the compiled module's does not.
"""Documented extension module.

Multi-line, with the closing quotes at column 0 -- so the text ends with a
bare newline, unlike `incr` below whose indented close leaves trailing
spaces. The two shapes render through different branches of the docstring
emitter, so both are pinned here.
"""
from enum import IntEnum

from tpy import Int64
from tpy.extern import export


class Rejected(ValueError):
    """Raised when the value is rejected."""


@export
class Level(IntEnum):
    """A documented enum -- set on the type after the functional API builds it."""

    LOW = 0
    HIGH = 1


@export
class Bare(IntEnum):
    UNSET = 0


@export
class Counter:
    """A counter with a documented surface."""

    value: Int64

    def __init__(self, value: Int64):
        """Start at `value`."""
        self.value = value

    def incr(self, by: Int64) -> None:
        """Add `by` to the counter.

        The second line survives too, minus the block's common indent --
        __doc__ is what the compiler makes of the literal, not the literal.
        """
        self.value += by

    def undocumented_method(self) -> Int64:
        return self.value

    @property
    def doubled(self) -> Int64:
        """Twice the current value."""
        return self.value * 2


@export
class Base:
    """A documented base class."""

    n: Int64

    def __init__(self, n: Int64):
        self.n = n

    def described(self) -> Int64:
        """Documented on the base, reached through the subclass."""
        return self.n


@export
class Derived(Base):
    """A documented subclass."""


@export
def documented(x: Int64) -> Int64:
    """Return `x` unchanged."""
    return x


@export
def unicode_doc(x: Int64) -> Int64:
    """Non-ASCII: naïve résumé, 10°, 中文."""
    return x


@export
def empty_doc(x: Int64) -> Int64:
    """"""
    return x


@export
def indented_doc(x: Int64) -> Int64:
    """   Leading whitespace on the first line always goes.

        Relative depth between the remaining lines survives:
          this one stays two columns deeper.
    """
    return x


@export
def undocumented(x: Int64) -> Int64:
    return x


@export
def reject() -> None:
    """Always raise."""
    raise Rejected("no")
