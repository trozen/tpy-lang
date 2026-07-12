# A property setter typed as a reference exposed class cannot cross: the value
# arrives as a borrow of the live argument payload, but the setter's parameter
# is an ownership transfer (Own auto-wrap) with nothing to move from. The
# located error replaces an opaque C++ build failure. (The getter returns a
# scalar so the case isolates the setter reject.)
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Inner:
    x: Int64

    def __init__(self) -> None:
        self.x = 0


@export
class Holder:
    _inner: Inner

    def __init__(self) -> None:
        self._inner = Inner()

    @property
    def inner(self) -> Int64:
        return self._inner.x

    @inner.setter
    def inner(self, v: Inner) -> None:  # tpyc: error(/property 'inner' setter takes exposed class 'Inner'/)
        self._inner = v
