# tpy: ext_module
# Identity-preserving borrow returns: a `-> Cls` return whose reference is
# the receiver or a parameter crosses as the ORIGINAL PyObject (Py_IncRef in
# the glue after an address match against self / the exposed-class params),
# so `is`, write-through, and the dynamic type survive the boundary --
# return-self, fluent chains, param pass-through, and both arms of a
# mixed self/param body. A source with no live PyObject behind it (the
# `_inner` field borrow) still copies; that residual divergence (and the
# first-field address-collision guard: Holder's payload starts at `_inner`,
# but Holder is unrelated to Inner so the glue must not compare them) is
# asserted ext-only in ext_checks.py.
from tpy import Int64, Own, readonly
from tpy.extern import export


@export
class Box:
    v: Int64

    def __init__(self, v: Int64):
        self.v = v

    def me(self) -> "Box":
        return self

    def bump(self) -> "Box":
        self.v = self.v + 1
        return self

    def pick(self, other: "Box", first: bool) -> "Box":
        if first:
            return self
        return other

    @property
    def itself(self) -> "Box":
        return self

    def view(self) -> "readonly[Box]":
        # readonly is a TPy-side contract (no mutation through this handle
        # inside TPy); across the boundary the identity path applies exactly
        # like the mutable form -- the ORIGINAL object crosses, matching the
        # plain-Python aliasing the cpy stubs exhibit (readonly is a no-op
        # there).
        return self


@export
class Inner:
    x: Int64

    def __init__(self, x: Int64):
        self.x = x


@export
class Holder:
    _inner: Inner

    def __init__(self, x: Int64):
        self._inner = Inner(x)

    def get_inner(self) -> Inner:
        return self._inner

    def pick_inner(self, other: Inner, use_field: bool) -> Inner:
        if use_field:
            return self._inner
        return other


default_box: Box = Box(0)


@export
def get_default() -> Box:
    # A module global has no PyObject behind it: the free fn has no class
    # params, so no candidates are emitted at all -- this copies (and warns
    # at compile; the interop harness doesn't pin diagnostics).
    return default_box


@export
def identity(b: Box) -> Box:
    return b


@export
def fresh(v: Int64) -> Own[Box]:
    return Box(v)
