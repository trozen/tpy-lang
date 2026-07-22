# Identity/view-preserving borrow returns: bare `self`/param returns and
# never-reassigned field borrows of them don't warn (methods, property
# getters, free fns, and every marshalled-return dunder slot -- getitem,
# iter, next thread the same candidates); reassignable fields and module
# globals still copy and warn. A VALUE-type class crosses by copy from
# every source (no identity/view path), so even its `return self` warns,
# with value-specific wording; a setitem return is discarded by the slot
# (nothing crosses), so it never warns.
# tpy: ext_module
from tpy import Int64, Own, ValueType, readonly
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
        # The mp_subscript slot threads the receiver candidate like a
        # method wrapper: a bare `self` return crosses by identity.
        return self  # tpyc: ok

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
        # A view-safe field source crosses as a borrow view from a dunder
        # slot too: `_b` is never reassigned.
        return self._b  # tpyc: ok


@export
class Rebound:
    _alt: Inner

    def __init__(self):
        self._alt = Inner(0)

    def reset(self) -> None:
        self._alt = Inner(1)

    def __getitem__(self, i: Int64) -> Inner:
        # Dunder-slot RESIDUE witness: a reassignable-field source has no
        # identity/view path, so dunder slots warn exactly like method
        # wrappers.
        return self._alt  # tpyc: warning(/no live object behind it/)

    def __setitem__(self, i: Int64, v: Int64) -> Inner:
        # The mp_ass_subscript slot discards the method's return (CPython
        # does too), so even a would-warn source never crosses here.
        return self._alt  # tpyc: ok


@export
class Flat(ValueType):
    n: Int64

    def __init__(self, n: Int64):
        self.n = n

    def itself(self) -> "readonly[Flat]":
        # A value class crosses by copy from EVERY source -- `return self`
        # included -- so the identity suppression must not apply and the
        # value-specific wording fires.
        return self  # tpyc: warning(/value-type class 'Flat' by reference, and a value class always crosses the CPython boundary as a copy/)

    def __getitem__(self, i: Int64) -> "readonly[Flat]":
        return self  # tpyc: warning(/value-type class 'Flat' by reference, and a value class always crosses/)


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
