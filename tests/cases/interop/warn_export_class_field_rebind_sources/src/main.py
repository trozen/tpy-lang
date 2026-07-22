# The never-reassigned view gate records a rebind from EVERY store shape:
# a subclass method (normal spelling), the unbound-self spelling
# (Base._b = ...), and a property setter all make the DECLARING record's
# field view-ineligible, so its borrow returns copy and warn.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
class Inner:
    x: Int64

    def __init__(self, x: Int64):
        self.x = x


@export
class Base:
    _a: Inner
    _b: Inner
    _c: Inner

    def __init__(self):
        self._a = Inner(1)
        self._b = Inner(2)
        self._c = Inner(3)

    def get_a(self) -> Inner:
        return self._a  # tpyc: warning(/no live object behind it/)

    def get_b(self) -> Inner:
        return self._b  # tpyc: warning(/no live object behind it/)

    def get_c(self) -> Inner:
        return self._c  # tpyc: warning(/no live object behind it/)

    @property
    def c(self) -> Int64:
        return self._c.x

    @c.setter
    def c(self, value: Int64) -> None:
        self._c = Inner(value)


@export
class Sub(Base):
    def clobber_a(self) -> None:
        self._a = Inner(0)

    def clobber_b(self) -> None:
        Base._b = Inner(0)
