# A held select's type and value category are the C++ compiler's: a
# select naming an arm is held by its address (no copy, no frame storage),
# one whose arm is converted is a fresh value built in place.
# A held operand whose `@error_return` unwrap is nested inside it is
# evaluated before the guard with that operand
# (BUGS.md#held-operand-nested-unwrap-eager), as is a template whose
# pattern names a jump: those sections run with the guard taken, or over a
# pure call, where that is unseen.
from typing import Iterator
from tpy import int32, int64, Own, StrView, error_return
from tpy.extern import cpp_template
from part_unwraps import E, er_i


def sub(sv: StrView) -> Iterator[StrView]:
    # Yields twice: a view that dangled after the first resume shows.
    yield sv
    yield sv


def junk(s: str) -> str:
    return s + "-junk-long-heap-string-0123456789"


def gen_names(xs: list[str], ws: list[str], zs: list[str]) -> Iterator[str]:
    # Generator: nested selects of elements name the elements, no frame
    # storage.
    for x in sub((xs[0] or ws[0]) or zs[0]):  # tpyc: ok
        yield "names " + str(x)


def gen_tern(xs: list[str], s: str, f: bool) -> Iterator[str]:
    # Generator: the frame owns `s` as a string, so the ternary names an
    # element or the frame's `s`: no copy, no frame storage.
    for x in sub((xs[0] if f else s) or xs[1]):  # tpyc: ok
        j = junk(s)
        yield "tern " + str(x) + j[0:1]


def plain_tern(xs: list[str], s: str, f: bool) -> None:
    # Function: the ternary copies the `str` parameter beside an element.
    v = asv_plain((s if f else xs[0]) or xs[1])  # tpyc: ok
    print("lowered-plain-tern", v)


def asv_plain(sv: StrView) -> str:
    return str(sv)


def widen(a: int32, b: int64, c: int64, flag: bool, guard: bool) -> int64:
    # The `int32` arm is converted: the held ternary is a value, built in
    # place under the guard.
    return ((a if flag else b) or c) if guard else 0  # tpyc: ok


def captured(xs: list[str], s: str, zs: list[str], guard: bool) -> str:
    def inner() -> str:
        # A captured `str` parameter copied beside an element.
        return ((xs[0] or s) or zs[0]) if guard else "none"  # tpyc: ok
    return inner()


def narrowed(xs: list[str], s: str | None, zs: list[str], guard: bool) -> None:
    if s is not None:
        # A narrowed `str | None` parameter beside an element.
        print("lowered-narrowed", ((xs[0] or s) or zs[0]) if guard else "none")  # tpyc: ok


def wide(big: int, big2: int, guard: bool) -> int:
    # The wide literal and `big` render one type: the inner `or` names the
    # held literal or `big`, held by its address (no copy of `big`).
    return ((123456789012345678901234567890 or big) or big2) if guard else 0  # tpyc: ok


class Q:
    v: int32
    # A heap member: a slot engaged or destroyed wrongly frees garbage.
    tag: str

    def __init__(self, v: int32) -> None:
        self.v = v
        self.tag = "q-heap-tag-long-string-0123456789"


def mkq(v: int32) -> Own[Q]:
    return Q(v)


@error_return(E)
def truth(n: int32) -> bool:
    if n < 0:
        raise E()
    return n != 0


@cpp_template("({{ int32_t v = {0}; if (v < 0) return int32_t(12); v; }})")
def checked_value(x: int32) -> int32: ...


@cpp_template("({{ int32_t v = {0}; if (v < 0) [[unlikely]] {{ return int32_t(12); }} v; }})")
def unlikely_value(x: int32) -> int32: ...


@cpp_template("({{ int32_t v = {0}; if (v < 0) break; v; }})")
def stop_neg(x: int32) -> int32: ...


# -- The unwrap nested inside the operand: evaluated before the guard.

@error_return(E)
def twice(g: bool, n: int32) -> int32:
    # An unwrap in the call's argument.
    return (er_i(er_i(n)) or 5) if g else 0  # tpyc: ok


@error_return(E)
def tpl_arg(g: bool, n: int32) -> int32:
    # A returning template in the call's argument (pure for `n >= 0`;
    # CPython's bodyless binding gives None, hence `or n`).
    return (er_i(checked_value(n) or n) or 5) if g else 0  # tpyc: ok


@error_return(E)
def nested(g: bool, a: int32, n: int32) -> int32:
    # Inside another held operand.
    return ((a or (er_i(n) or 7)) or 9) if g else 1  # tpyc: ok


@error_return(E)
def arm(g: bool, n: int32) -> int32:
    # In a ternary arm, under a fresh root.
    return ((1 if n > 5 else mkq(er_i(n)).v) or 5) if g else 0  # tpyc: ok


@error_return(E)
def inner_or(g: bool, n: int32) -> int32:
    # In the right operand of the held operand's own `or`.
    return (((n + 1) or mkq(er_i(n)).v) or 5) if g else 0  # tpyc: ok


@error_return(E)
def elem(g: bool, n: int32, xs: list[str]) -> str:
    # In a subscript index; the element is bound by reference, no copy.
    return (xs[er_i(n)] or "d") if g else "e"  # tpyc: ok


@error_return(E)
def pick_big(g: bool, a: int, b: int, n: int32) -> int:
    # In a ternary test over two `int` parameters: no BigInt copy.
    return ((a if truth(n) else b) or b) if g else b  # tpyc: ok


@error_return(E)
def opt_bytes(a: bytes | None, b: bytes | None, n: int32, g: bool) -> int32:
    if a is not None and b is not None:
        # Narrowed views: the select is a view, never an owned copy.
        return len((a if truth(n) else b) or b) if g else 0  # tpyc: ok
    return 0


@error_return(E)
def walrus(g: bool, n: int32) -> int32:
    # Under a walrus.
    return ((y := er_i(n)) or 42) if g else 17  # tpyc: ok


@error_return(E)
def walrus_test(g: bool, n: int32) -> int32:
    # Under a walrus in an `if` test's `and`.
    if g and ((y := er_i(n)) or 5) > 3:  # tpyc: ok
        return 1
    return 0


@error_return(E)
def comp(g: bool, n: int32) -> int32:
    # In a comprehension.
    return (len([er_i(i) or 7 for i in range(n)]) or 42) if g else 17  # tpyc: ok


@error_return(E)
def chain(g: bool, n: int32) -> int32:
    # Under a chained comparison's fresh root.
    return ((7 if 0 < mkq(er_i(n)).v < 9 else 8) or n) if g else 17  # tpyc: ok


def tpl(g: bool, x: int32) -> int32:
    # A template whose statement expression returns (0: CPython's bodyless
    # binding gives None, which takes `9` too; the binding is pure).
    return (checked_value(x) or 9) if g else 3  # tpyc: ok


def tpl_unlikely(g: bool, x: int32) -> int32:
    # A return behind an attribute: bound before the statement, as `tpl`.
    return (unlikely_value(x) or 9) if g else 3  # tpyc: ok


def tpl_break(g: bool, xs: list[int32]) -> int32:
    # A template breaking out of the enclosing loop: bound before the
    # statement, inside the loop.
    total = 0
    for x in xs:
        total += (stop_neg(x) or 9) if g else 3  # tpyc: ok
    return total


@error_return(E)
def case_c(k: int32, n: int32, x: int) -> str:
    # One call per `k`, the guard taken: a raise names the call raising.
    if k == 0:
        return str(nested(True, 0, n))
    if k == 1:
        return str(nested(True, 4, n))
    if k == 2:
        return str(arm(True, n))
    if k == 3:
        return str(inner_or(True, n))
    if k == 4:
        return elem(True, n, ["a" * 40, ""])
    if k == 5:
        return str(pick_big(True, x, 4, n))
    if k == 6:
        return str(opt_bytes(b"first", b"second", n, True))
    if k == 7:
        return str(walrus(True, n))
    if k == 8:
        return str(walrus_test(True, n))
    if k == 9:
        return str(comp(True, n))
    if k == 10:
        return str(chain(True, n))
    if k == 11:
        return str(twice(True, n))
    return str(tpl_arg(True, abs(n)))


def main() -> None:
    long_s = "param-long-heap-string-0123456789abcdef"
    for v in gen_names([""], [""], ["zlong-heap-string-0123456789abcdefgh"]):
        print("lowered-gen", v)
    xs = ["", "elem-long-heap-string-0123456789abcdefgh"]
    for f in [True, False]:
        for v in gen_tern(xs, long_s, f):
            print("lowered-gen", v)
        plain_tern(xs, long_s, f)
    print("lowered-widen", widen(1, 2, 3, True, True),
          widen(0, 2, 3, True, True), widen(1, 2, 3, False, False))
    print("lowered-captured", captured(["", "q"], "param", ["z"], True),
          captured(["x"], "param", ["z"], False))
    narrowed(["", "q"], long_s, ["z"], True)
    narrowed(["e"], long_s, ["z"], False)
    narrowed(["e"], "", ["z"], False)
    print("lowered-wide", wide(2, 3, True), wide(2, 3, False))
    x: int = 123456789012345678901234
    # One `try` per call, so each raising path runs.
    for n in [1, -1, 0]:
        for k in range(13):
            try:
                r_c = case_c(k, n, x)
                print("unwraps-c", k, n, r_c)
            except E:
                print("unwraps-c raised", k, n)
    # A false guard with a truthy `a`: the inner unwrap is not reached.
    try:
        r_skip = nested(False, 3, 2)
        print("unwraps-c nested skip", r_skip)
    except E:
        print("unwraps-c nested skip raised")
    print("unwraps-c tpl", tpl(True, 0), tpl(False, 0), tpl_unlikely(True, 0),
          tpl_unlikely(False, 0), tpl_break(True, [0, 0]), tpl_break(False, [0]))
