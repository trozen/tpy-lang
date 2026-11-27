# Keyword-only and `*args` slots of a template, and union defaults no C++
# declaration renders (a binding's, a non-trailing keyword-only one).
from typing import TypedDict, Unpack
from tpy import int32
from tpy.extern import cpp_template, native
from fixtures import mark_i


@cpp_template("({0} + {1})")
def addk(a: int32, *, k: int32) -> int32: ...  # tpyc: ok


# The pack is slot 1, before the keyword-only `k` (slot 2).
@cpp_template("(static_cast<void>({1}), {0} + {2})")
def packed(a: int32, *xs: int32, k: int32) -> int32: ...  # tpyc: ok


class K(TypedDict):
    k: int32


# Every slot kind numbered in order: `a`, the pack, `b`, the `**kw` object.
@cpp_template("({0} + {1}[0] + {2} + {3}.k)")
def every(a: int32, *xs: int32, b: int32, **kw: Unpack[K]) -> int32: ...  # tpyc: ok


# Never called: a binding's union default is filled at each call.
@native("tpy_template_case_absent")
def absent(x: int32 | str = 1, y: int32 = 3) -> int32: ...  # tpyc: ok


# `a` defaults before the required `b`, so the C++ declaration has no default.
def kwf(*, a: int32 | str = 1, b: int32) -> int32:  # tpyc: ok
    return b


def main() -> None:
    # keyword-only argument
    r = addk(1, k=mark_i("kwonly", 2))  # tpyc: ok
    print("kwonly", "done")
    # an element of the `*args` pack
    s = packed(1, 2, mark_i("pack", 3), k=4)  # tpyc: ok
    print("pack", "done")
    # a keyword-only union default of a plain function
    print("kwdefault", kwf(b=2))  # tpyc: ok
    # the `**kw` object, read through its own placeholder
    t = every(1, 2, b=3, k=mark_i("kwargs", 30))  # tpyc: ok
    print("kwargs", "done")
