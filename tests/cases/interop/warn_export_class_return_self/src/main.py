# Identity/view-preserving borrow returns: bare `self`/param returns and
# never-reassigned field borrows of them don't warn (methods, property
# getters, free fns, and the tp_iter slot); reassignable fields, module
# globals, and the other dunder slots still copy and warn.
# tpy: ext_module
from tpy import Int64, Own
from tpy.extern import export


@export
class Inner:
    x: Int64

    def __init__(self, x: Int64):
        self.x = x


@export
class Box:
    v: Int64
    _inner: Inner
    _alt: Inner

    def __init__(self, v: Int64):
        self.v = v
        self._inner = Inner(v)
        self._alt = Inner(v)

    def me(self) -> "Box":
        return self  # tpyc: ok

    @property
    def itself(self) -> "Box":
        return self  # tpyc: ok

    def get_inner(self) -> Inner:
        # `_inner` is never reassigned outside __init__, so this crosses as
        # an aliasing borrow view -- no copy, no warning.
        return self._inner  # tpyc: ok

    def pick_inner(self, other: Inner, use_field: bool) -> Inner:
        if use_field:
            return self._inner  # tpyc: ok
        return other

    def swap_alt(self) -> None:
        # Post-__init__ reassignment makes `_alt` view-ineligible: a live
        # view would read the storage slot through this rebind.
        self._alt = Inner(0)

    def get_alt(self) -> Inner:
        return self._alt  # tpyc: warning(/no live object behind it/)

    def __iter__(self) -> "Box":
        # tp_iter threads the receiver candidate, so the canonical
        # return-self iterator crosses by identity and doesn't warn.
        return self  # tpyc: ok

    def __getitem__(self, i: Int64) -> "Box":
        # Every other dunder slot emits through the expression-form copy
        # path (no identity candidates), so even a bare `self` return warns,
        # with the always-copies wording (no identity advice).
        return self  # tpyc: warning(/dunder slot with no identity-preserving path/)

    def __next__(self) -> Int64:
        if self.v <= 0:
            raise StopIteration
        self.v -= 1
        return self.v


@export
class FieldIter:
    _b: Box

    def __init__(self):
        self._b = Box(0)

    def __iter__(self) -> Box:
        # The __iter__ carve-out admits view-safe field sources too:
        # `_b` is never reassigned, so the iterator crosses as a view.
        return self._b  # tpyc: ok


self = Box(0)


@export
def identity(b: Box) -> Box:
    return b  # tpyc: ok


@export
def through_param(b: Box) -> Inner:
    # A never-reassigned field of a PARAM is view-safe too (owner = the
    # param's PyObject, found by the address-range scan).
    return b._inner  # tpyc: ok


@export
def get_global() -> Box:
    # A free function has no receiver: a module global -- even one named
    # `self` -- is not a glue candidate, so this copies and warns.
    return self  # tpyc: warning(/no live object behind it/)


@export
def fresh(v: Int64) -> Own[Box]:
    return Box(v)  # tpyc: ok
