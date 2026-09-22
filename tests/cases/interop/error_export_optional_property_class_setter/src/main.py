# An `Optional[Cls]` property setter is the exposed-class setter case behind
# the None gate: the value arrives as a borrow of the live argument payload,
# which the setter's ownership-transfer parameter cannot move from -- the
# dedicated setter message, not the generic Own advice a setter cannot spell.
# tpy: ext_module
from typing import Optional
from tpy import int32
from tpy.extern import export


@export
class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


@export
class Holder:
    _inner: Optional[Inner]

    def __init__(self) -> None:
        self._inner = None

    @property
    def inner(self) -> int32:
        return -1 if self._inner is None else self._inner.v

    @inner.setter
    def inner(self, v: Optional[Inner]) -> None:  # tpyc: error(/setter takes exposed class 'Inner'/)
        self._inner = v
