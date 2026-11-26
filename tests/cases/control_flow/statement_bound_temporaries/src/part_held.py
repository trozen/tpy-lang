# A held `and` / `or` operand banked under a guard: its slot takes the
# type and value category the C++ compiler gives the operand -- an lvalue
# or an xvalue held by its address (no copy, no move), a prvalue built in
# place -- and it runs only when the guard passes.
from typing import ClassVar, Final, Iterator
from tpy import int32, int64, Own, StrView
from fixtures import P, side
from tpy.extern import cpp_template


class K:
    # Class constants read through a fresh receiver: a static, so the
    # receiver is evaluated for its effect only.
    NAME: Final[StrView] = "nm"
    LIM: ClassVar[int] = 7

    def __init__(self) -> None:
        print("held-mk K")


def mkk() -> Own[K]:
    return K()


calls: list[int32] = [0]


def counted(n: int32) -> int32:
    calls[0] += 1
    return n


@cpp_template("({{ int32_t t = {0}; t * t; }})")
def square(a: int32) -> int32: ...


def rebound(s: str, t: str, guard: bool) -> str:
    # A rebound `str` parameter (an owned string) beside a view.
    s = t + "-rebound-long-heap-string-0123456789"
    return ((s or t) or t) if guard else "z"  # tpyc: ok


def captured(s: str, t: str, guard: bool) -> str:
    s = t + "-captured-long-heap-string-0123456789"

    def read() -> str:
        # The same select over captures.
        return ((s or t) or t) if guard else "skipped"  # tpyc: ok
    return read()


def subscripts(a: bytes, d: dict[int32, int32], t: tuple[int32, int32, int32],
               flag: bool) -> int32:
    # Element reads of bytes, a dict and a tuple: lvalues and prvalues.
    b_r = ((a[0] or a[1]) or a[2]) if flag else a[0]  # tpyc: ok
    d_r = ((d[1] or d[2]) or d[3]) if flag else 0  # tpyc: ok
    t_r = ((t[0] or t[1]) or t[2]) if flag else 0  # tpyc: ok
    return b_r + d_r + t_r


def widen(a: int32, b: int64, c: int64, flag: bool, guard: bool) -> int64:
    # A ternary whose `int32` arm converts: a prvalue built in place.
    return ((a if flag else b) or c) if guard else 0  # tpyc: ok


def widen_float(a: int32, b: float, c: float, flag: bool, guard: bool) -> float:
    # The same toward `float`.
    return ((a if flag else b) or c) if guard else 0.0  # tpyc: ok


def widen_big(a: int32, b: int, c: int, flag: bool, guard: bool) -> int:
    # The same toward `int`: the converted arm is a fresh BigInt.
    return ((a if flag else b) or c) if guard else 0  # tpyc: ok


def big(a: int, b: int, c: int, flag: bool, guard: bool) -> int:
    # A ternary of two `int` parameters names one of them: no BigInt copy.
    return ((a if flag else b) or c) if guard else b  # tpyc: ok


def big_id(a: int, b: int, c: int, flag: bool, guard: bool) -> int:
    # An identity conversion of a parameter names it too: no copy.
    return ((int(a) if flag else b) or c) if guard else b  # tpyc: ok


def literals(s: str, u: str, guard: bool) -> str:
    # A string literal held operand keeps a slot of its view type.
    a_r = (("" or s) or u) if guard else "z"  # tpyc: ok
    b_r = ((s or "lit") or u) if guard else "z"  # tpyc: ok
    return a_r + "|" + b_r


def gen_names(s: str, t: str, f: bool, guard: bool) -> Iterator[str]:
    # A generator body: the held select is a temp of its statement.
    yield ((s if f else t) or t) if guard else "z"  # tpyc: ok
    yield ((s or t) or t) if guard else "z"  # tpyc: ok


def gen_len(s: bytes, t: bytes, guard: bool) -> Iterator[int32]:
    yield len((s or t) or t) if guard else 0  # tpyc: ok


def guards(g: bool, d: dict[str, int32]) -> None:
    calls[0] = 0
    # A call in a guarded operand runs only when the guard passes.
    c_r = (counted(0) or 5) if g else 0  # tpyc: ok
    print("held-counter", c_r, calls[0])
    # An element read behind a false guard does not look the key up.
    k_r = (d["k"] or 5) if g else 0  # tpyc: ok
    print("held-dict", k_r)
    # A walrus target is assigned only when the guard passes.
    n = -1
    w_r = ((n := counted(3) + 1) or 2) if g else 0  # tpyc: ok
    print("held-walrus", w_r, n, calls[0])


def templated[T](a: int32, b: T, g: bool) -> int32:
    # A `@cpp_template` statement expression in a generic body (`a` is 0:
    # the bodyless binding gives CPython None, which takes `2` too).
    return (square(a) or 2) if g else 0  # tpyc: ok


def constants(t: StrView, n: int, guard: bool) -> None:
    # The receiver of a class constant: built under the guard, for its
    # effect only (a const prvalue under g++).
    r = ((mkk().NAME or t) or t) if guard else t  # tpyc: ok
    q = ((mkk().LIM or n) or n) if guard else 0  # tpyc: ok
    print("held-const", r, q)


def bump(items: list[int32]) -> None:
    items.append(9)


def mutate(a: list[int32], b: list[int32], c: list[int32], guard: bool) -> None:
    # A reference select read through its held operand's address: the
    # callee appends to the caller's list itself.
    bump(((a or b) or c) if guard else c)  # tpyc: ok
    print("held-mutate", a, b, c)


@cpp_template("std::move({0})")
def as_xvalue(x: int) -> int: ...


@cpp_template("std::move({0})")
def as_xstr(x: str) -> str: ...


def xvalues(x: int, t: str, g: bool) -> None:
    n = x + 1
    s = t + "-xvalue-long-heap-string-0123456789"
    # An xvalue operand is held by its address: `n` and `s` are not moved
    # from. (CPython's bodyless binding gives None, which takes the
    # fallback: only truthiness is printed.)
    r = (as_xvalue(n) or 2) if g else 2  # tpyc: ok
    q = (as_xstr(s) or "d") if g else "e"  # tpyc: ok
    print("held-xvalue", r > 0, len(q) > 0, n, s)


def xvalue_param(x: int, g: bool) -> bool:
    # Over a const parameter: held by its address, no copy.
    return ((as_xvalue(x) or 2) if g else 2) > 0  # tpyc: ok


@cpp_template("{0}.n")
def p_n(p: Own[P]) -> int32: ...


def member_of_fresh(g: bool) -> bool:
    # A template member of a fresh argument, an xvalue of a temporary: its
    # type query returns it without a dangling-return warning, and the
    # argument is built only under the guard. (CPython's bodyless binding
    # gives None, which takes the fallback: only truthiness is printed.)
    return ((p_n(side("held-member", 3)) or 5) if g else 0) > 0  # tpyc: ok


def main() -> None:
    member_taken = member_of_fresh(True)
    member_skipped = member_of_fresh(False)
    print("held-member", member_taken, member_skipped)
    print("held-rebound", rebound("one", "two", True), rebound("one", "", False))
    print("held-captured", captured("one", "two", True),
          captured("one", "two", False))
    print("held-subscripts", subscripts(b"\x00\x05\x09", {1: 0, 2: 7, 3: 9},
                                        (0, 4, 8), True),
          subscripts(b"\x00\x05\x09", {}, (0, 4, 8), False))
    print("held-widen-64", widen(1, 2, 3, True, True),
          widen(0, 2, 3, True, True), widen(1, 2, 3, False, False))
    # `int()` of the converted arm: CPython hands the `int32` back as is.
    print("held-widen", int(widen_float(1, 2.0, 3.0, True, True)),
          widen_float(0, 2.0, 3.0, True, True),
          widen_float(1, 2.0, 3.0, False, False))
    print("held-widen-big", widen_big(1, 2, 3, True, True),
          widen_big(0, 2, 3, True, True), widen_big(1, 2, 3, False, False))
    x: int = 123456789012345678901234
    print("held-big", big(x, 4, 5, True, True), big(0, x, 5, False, True),
          big(x, 4, 5, True, False))
    print("held-big-id", big_id(x, 4, 5, True, True),
          big_id(0, x, 5, True, True), big_id(x, 4, 5, True, False))
    print("held-literals", literals("s", "u", True), literals("", "u", True),
          literals("s", "u", False))
    for v in gen_names("one", "two", True, True):
        print("held-gen", v)
    for v in gen_names("", "two", False, False):
        print("held-gen", v)
    for v in gen_names("", "two", False, True):
        print("held-gen", v)
    for k in gen_len(b"", b"bb", True):
        print("held-gen-len", k)
    guards(False, {})
    guards(True, {"k": 0})
    print("held-template", templated(0, 5, True), templated(0, 5, False))
    constants("t", 3, True)
    constants("t", 3, False)
    mutate([1], [2], [3], True)
    mutate([], [2], [3], True)
    mutate([], [], [3], True)
    mutate([1], [2], [3], False)
    xvalues(123456789012345678901234567890, "ab", True)
    xvalues(5, "ab", False)
    print("held-xvalue-param", xvalue_param(x, True), xvalue_param(x, False), x)
