# A default's union landing is published only where a C++ declaration
# renders it, decided once every default of the signature is settled: an
# overload copy prunes a later default its type refuses, which takes the
# earlier union default out of the trailing suffix too.
from typing import Final, overload
from tpy import int32, int64, ValueType, readonly
from tpy.extern import native


@overload
def f(a: int32 | str = 1, b: str = "x") -> int32: ...
@overload
def f(a: int32 = 1, b: int32 = 2) -> int32: ...
def f(a: int32 | str = 1, b: int32 | str = 2) -> int32:  # tpyc: ok
    return 1


class C:
    def __init__(self) -> None:
        pass

    @overload
    def m(self, a: int32 | str = 1, b: str = "x") -> int32: ...
    @overload
    def m(self, a: int32 = 1, b: int32 = 2) -> int32: ...
    def m(self, a: int32 | str = 1, b: int32 | str = 2) -> int32:  # tpyc: ok
        return 2


# The pruned default is the EARLIER one (`a` of the first copy): the union
# default after it stays in the trailing suffix and renders.
@overload
def g(a: str = "x", b: int64 | str = 2) -> int64: ...
@overload
def g(a: int32 = 7, b: int64 | str = 2) -> int64: ...
def g(a: str | int32 = 7, b: int64 | str = 2) -> int64:  # tpyc: ok
    if isinstance(b, str):
        return 0
    return b


class D:
    def __init__(self) -> None:
        pass

    # The record-method twin of `g`: the earlier slot pruned, the later union
    # default still rendered.
    @overload
    def g(self, a: str = "x", b: int64 | str = 2) -> int64: ...
    @overload
    def g(self, a: int32 = 7, b: int64 | str = 2) -> int64: ...
    def g(self, a: str | int32 = 7, b: int64 | str = 2) -> int64:  # tpyc: ok
        if isinstance(b, str):
            return 0
        return b


K: Final[int32] = 7


# Union defaults naming a constant on either side of a required keyword-only
# parameter: only the trailing one is in the C++ default suffix.
def named(*, early: int64 | str = K, required: int32, late: int64 | str = K) -> int64:  # tpyc: ok
    if isinstance(early, str):
        return 0
    if isinstance(late, str):
        return 0
    return early + late + required


class Named:
    def __init__(self) -> None:
        pass

    # The record-method twin of `named`.
    def named(self, *, early: int64 | str = K, required: int32, late: int64 | str = K) -> int64:  # tpyc: ok
        if isinstance(early, str):
            return 0
        if isinstance(late, str):
            return 0
        return early + late + required


class VBox(ValueType):
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


# A union default holding a ValueType record has no spelling where a free
# function is declared, with or without `readonly`.
def boxed(a: VBox | int32 = 1) -> int32:  # tpyc: ok
    if isinstance(a, VBox):
        return a.v
    return a


def boxed_ro(a: readonly[VBox | int32] = 2) -> int32:  # tpyc: ok
    if isinstance(a, VBox):
        return a.v
    return a


# The readonly Optional face, before a later union default.
def boxed_opt(a: readonly[VBox | None] = None, b: int32 | str = 3) -> int32:  # tpyc: ok
    if isinstance(b, str):
        return 0
    if a is None:
        return b
    return a.v


# Overload copies whose union default names a constant.
@overload
def h(a: str, b: int64 | str = K) -> int64: ...
@overload
def h(a: int32, b: int64 | str = K) -> int64: ...
def h(a: str | int32, b: int64 | str = K) -> int64:  # tpyc: ok
    if isinstance(b, str):
        return 0
    return b


# A binding's union default is filled at the call that omits it.
@native("::tpy::repr_of")
def shown(x: int32 | str = 1) -> str: ...  # tpyc: ok


def main() -> None:
    # free-function copy: the first copy's `b` default is pruned
    print("copy free", f(1, "y"), f())  # tpyc: ok
    # method copy: the same prune on a record method
    c = C()
    print("copy method", c.m(1, "y"), c.m())  # tpyc: ok
    # kept suffix: the union default after a pruned earlier slot
    k = g("x")  # tpyc: ok
    print("kept suffix", k)
    # kept suffix on a record method
    print("kept suffix method", D().g("x"))  # tpyc: ok
    # constant union defaults around a required keyword-only parameter
    print("named slots", named(required=1))  # tpyc: ok
    # the record-method twin of the keyword-only constant defaults
    print("named method", Named().named(required=1))  # tpyc: ok
    # ValueType record union defaults, plain, readonly and readonly Optional
    print("boxed", boxed(), boxed(VBox(4)))  # tpyc: ok
    print("boxed readonly", boxed_ro(), boxed_ro(VBox(5)))  # tpyc: ok
    print("boxed optional", boxed_opt(), boxed_opt(VBox(6)))  # tpyc: ok
    # overload copies with a constant union default
    print("copy constant", h("x"), h(1))  # tpyc: ok
    # the @native union default, omitted at the call
    s = shown()  # tpyc: ok
    print("native default", "done")
