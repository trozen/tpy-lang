# tpy: ext_module
# A docstring carrying an embedded NUL cannot cross: every CPython doc slot is
# a NUL-terminated C string, so the glue drops it (__doc__ stays None) rather
# than hand the host a silently truncated text. The drop always warns -- at
# every site that carries one: module, function, class, method, property, enum
# and exception class.
"main\0doc"
from enum import IntEnum

from tpy import Int64
from tpy.extern import export


@export
def has_nul(x: Int64) -> Int64:  # tpyc: warning(/docstring will not be visible from Python/)
    "bad\0doc"
    return x


@export
def clean(x: Int64) -> Int64:  # tpyc: ok
    """Fine."""
    return x


@export
class Holder:  # tpyc: warning(/@export class 'Holder': the docstring will not be visible/)
    "cls\0doc"

    v: Int64

    def __init__(self, v: Int64):
        self.v = v

    def meth(self) -> Int64:  # tpyc: warning(/method 'meth': the docstring will not be visible/)
        "meth\0doc"
        return self.v

    @property
    def prop(self) -> Int64:  # tpyc: warning(/property 'prop': the docstring will not be visible/)
        "prop\0doc"
        return self.v


@export
class BadEnum(IntEnum):  # tpyc: warning(/@export enum 'BadEnum': the docstring will not be visible/)
    "enum\0doc"

    A = 1


class BadDoc(ValueError):  # tpyc: warning(/docstring will not be visible from Python/)
    "exc\0doc"


@export
def boom() -> None:  # tpyc: ok
    raise BadDoc("x")
