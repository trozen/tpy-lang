# Identity-preserving borrow returns: bare `self`/param returns don't warn
# (methods, property getters, free fns, and the tp_iter slot); other dunder
# slots, field borrows, and module globals still copy and warn.
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

    def __init__(self, v: Int64):
        self.v = v
        self._inner = Inner(v)

    def me(self) -> "Box":
        return self  # tpyc: ok

    @property
    def itself(self) -> "Box":
        return self  # tpyc: ok

    def get_inner(self) -> Inner:
        return self._inner  # tpyc: warning(/copied across the CPython boundary there/)

    def pick_inner(self, other: Inner, use_field: bool) -> Inner:
        if use_field:
            return self._inner  # tpyc: warning(/on at least one return path/)
        return other

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
        # The __iter__ carve-out suppresses only bare-self bodies: a field
        # source has no live PyObject behind it, so it still copies and warns.
        return self._b  # tpyc: warning(/copied across the CPython boundary there/)


self = Box(0)


@export
def identity(b: Box) -> Box:
    return b  # tpyc: ok


@export
def get_global() -> Box:
    # A free function has no receiver: a module global -- even one named
    # `self` -- is not a glue candidate, so this copies and warns.
    return self  # tpyc: warning(/copied across the CPython boundary there/)


@export
def fresh(v: Int64) -> Own[Box]:
    return Box(v)  # tpyc: ok
