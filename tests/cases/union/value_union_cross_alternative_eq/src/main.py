# A value union compares BY VALUE across alternatives, as CPython does:
# `int32 | float64` holding 1 equals one holding 1.0. Each section names the
# position or shape it covers; the compares are the subject lines.
import asyncio
from dataclasses import dataclass
from typing import Iterator

from tpy import (Array, float64, int32, int64, ReturnException, uint8,
                 uint32, ValueType, error_return, readonly)

A: int32 | float64 = 1
B: int32 | float64 = 1.0
module_eq = A == B  # module-level statement  # tpyc: ok


class Boom(Exception, ReturnException):
    pass


class Guard:
    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# The eleven positions that lower. The twelfth, a comprehension holding
# such a compare, rejects at `expr.list_comp`
# (BUGS.md#list-comp-rejects-value-union-compare).
def free_fn(a: int32 | float64, b: int32 | float64) -> bool:  # free function
    return a == b  # tpyc: ok


class Holder:
    flag: bool

    def __init__(self, a: int32 | float64, b: int32 | float64):
        self.flag = a == b  # constructor  # tpyc: ok

    def method(self, a: int32 | float64, b: int32 | float64) -> bool:  # method
        return a == b  # tpyc: ok


def gen(a: int32 | float64, b: int32 | float64) -> Iterator[bool]:  # generator
    yield a == b  # tpyc: ok


async def in_async(a: int32 | float64, b: int32 | float64) -> bool:  # async
    return a == b  # tpyc: ok


def in_with(a: int32 | float64, b: int32 | float64) -> bool:  # with body
    with Guard():
        return a == b  # tpyc: ok


def in_closure(a: int32 | float64, b: int32 | float64) -> bool:  # closure
    def inner() -> bool:
        return a == b  # tpyc: ok

    return inner()


def in_try(a: int32 | float64, b: int32 | float64) -> bool:  # try/finally
    try:
        return a == b  # tpyc: ok
    finally:
        pass


@error_return(Boom)
def in_error_return(a: int32 | float64, b: int32 | float64) -> bool:  # @error_return
    return a == b  # tpyc: ok


def in_match(a: int32 | float64, b: int32 | float64) -> bool:  # match arm
    tag: int32 = 1
    match tag:
        case 1:
            return a == b  # tpyc: ok
        case _:
            return False


# Numeric families: the union answer beside the monomorphic twin's, which is
# what the runtime leaf is defined to reproduce.
def u_i32_f64(a: int32 | float64, b: int32 | float64) -> bool:
    return a == b  # tpyc: ok


def t_i32_f64(a: int32, b: float64) -> bool:
    return a == b  # tpyc: ok


def u_i32_i64(a: int32 | int64, b: int32 | int64) -> bool:
    return a == b  # tpyc: ok


def t_i32_i64(a: int32, b: int64) -> bool:
    return a == b  # tpyc: ok


def u_bool_i32(a: bool | int32, b: bool | int32) -> bool:
    return a == b  # tpyc: ok


def t_bool_i32(a: bool, b: int32) -> bool:
    return a == b  # tpyc: ok


def u_u8_i32(a: uint8 | int32, b: uint8 | int32) -> bool:
    return a == b  # tpyc: ok


def t_u8_i32(a: uint8, b: int32) -> bool:
    # The twin warns where the union does not: the warning is a mixed-sign
    # SOURCE-level hint, and the union's members are not written as operands.
    return a == b  # tpyc: warning(/signed and unsigned/)


def u_u32_i32(a: uint32 | int32, b: uint32 | int32) -> bool:
    return a == b  # tpyc: ok


def t_u32_i32(a: uint32, b: int32) -> bool:
    # Same mixed-sign hint at the 32-bit width.
    return a == b  # tpyc: warning(/signed and unsigned/)


def u_int_f64(a: int | float64, b: int | float64) -> bool:
    return a == b  # tpyc: ok


def t_int_f64(a: int, b: float64) -> bool:
    return a == b  # tpyc: ok


def u_ne(a: int32 | float64, b: int32 | float64) -> bool:
    return a != b  # tpyc: ok


# readonly[] is a const qualifier, not a shape change, so it routes the same.
def u_readonly(a: readonly[int32 | float64], b: readonly[int32 | float64]) -> bool:
    return a == b  # tpyc: ok


# Containers: the standard operators reach the nested variant's own.
def list_eq(xs: list[int32 | float64], ys: list[int32 | float64]) -> bool:
    return xs == ys  # tpyc: ok


def append_float(xs: list[int32 | float64]) -> None:
    v: int32 | float64 = 2.0
    xs.append(v)


def list_of_tuple_eq(xs: list[tuple[int32 | float64, int32]],
                     ys: list[tuple[int32 | float64, int32]]) -> bool:
    return xs == ys  # tpyc: ok


def dict_eq(ds: dict[str, int32 | float64],
            es: dict[str, int32 | float64]) -> bool:
    return ds == es  # tpyc: ok


def array_eq(a: Array[int32 | float64, 2], b: Array[int32 | float64, 2]) -> bool:
    return a == b  # tpyc: ok


# The GENERIC recursive-alias wrapper: its own operator==, not the compare
# gate.
type Tree[T] = T | list[Tree[T]]


def tree_eq(a: Tree[int32 | float64], b: Tree[int32 | float64]) -> bool:
    return a == b  # tpyc: ok


# The NON-generic recursive-alias wrapper is a separate emit shape, reached
# only indirectly: a direct `xs[0] == ys[0]` on two `V` values rejects at
# `binop.shape.==` and no case pins that (the adjacent
# `error_nongeneric_recursive_alias_pair_compare` stops earlier, at a bare
# `a: Json = ...` declaration's `decl.slot_type`), so the list compare is
# what reaches V::operator==.
type V = int | float | str | list[V]


def v_list_eq(xs: list[V], ys: list[V]) -> bool:
    return xs == ys  # tpyc: ok


def drop_last_v(xs: list[V]) -> None:
    xs.pop()


# A union of two RECORDS that DO define `__eq__`, through the wrapper and
# through a dict value. A record WITHOUT `__eq__` cannot be a section here:
# it stays a toolchain error with no TPy location
# (BUGS.md#container-compare-record-without-eq).
class Circle:
    r: int32

    def __init__(self, r: int32) -> None:
        self.r = r

    def __eq__(self, other: "Circle") -> bool:
        # Folds to True under TPy (`other` is typed Circle) and is a real
        # check under CPython, which routes the cross-alternative pair here
        # where TPy answers False without calling the dunder.
        if not isinstance(other, Circle):
            return False
        return self.r == other.r


class Square:
    side: int32

    def __init__(self, side: int32) -> None:
        self.side = side

    def __eq__(self, other: "Square") -> bool:
        if not isinstance(other, Square):
            return False
        return self.side == other.side


type Shape = Circle | Square


def shape_list_eq(xs: list[Shape], ys: list[Shape]) -> bool:
    return xs == ys  # tpyc: ok


# Read-only unlike its list siblings: no dict mutation lowers at this shape,
# so there is no post-boundary change to observe.
def shape_dict_eq(a: dict[str, Shape], b: dict[str, Shape]) -> bool:
    return a == b  # tpyc: ok


def clear_shapes(xs: list[Shape]) -> None:
    xs.clear()


# The recursive-alias wrapper with RECORD leaves: the `Shape` pair above is a
# plain (non-recursive) union, so this is the only section reaching a record
# compare THROUGH the wrapper's `::tpy::Union` member.
type ShapeTree = Circle | Square | list[ShapeTree]


def shape_tree_eq(xs: list[ShapeTree], ys: list[ShapeTree]) -> bool:
    return xs == ys  # tpyc: ok


# Ordering across alternatives.
def u_lt(a: int32 | float64, b: int32 | float64) -> bool:
    return a < b  # tpyc: ok


def u_le(a: int32 | float64, b: int32 | float64) -> bool:
    return a <= b  # tpyc: ok


def u_gt(a: int32 | float64, b: int32 | float64) -> bool:
    return a > b  # tpyc: ok


def u_ge(a: int32 | float64, b: int32 | float64) -> bool:
    return a >= b  # tpyc: ok


# A NaN operand: the ordering ops apply the operator, not a reduction onto
# `<` (`a <= b` and `not (b < a)` disagree on NaN), so the union answer must
# stay the twin's.
def t_le(a: float64, b: float64) -> bool:
    return a <= b  # tpyc: ok


def t_ge(a: float64, b: float64) -> bool:
    return a >= b  # tpyc: ok


# An unorderable SAME-alternative pair raises at RUNTIME, not at compile
# time: CPython raises for `Fixed() < Fixed()` on a type with no ordering,
# so the raise is the parity answer (unlike equality, where Python falls
# back to identity and the runtime has no answer to give). The monomorphic
# twin is stricter -- a bare `Fixed < Fixed` is a located sema error, "no
# '__lt__' method defined" -- so the union path is looser here; recorded on
# BUGS.md#value-union-no-equatable-conformance.
@dataclass(frozen=True)
class Fixed(ValueType):
    off: int32


@dataclass(frozen=True)
class Zone(ValueType):
    zid: int32


# `__ne__` INVERTED on purpose: CPython calls a declared `__ne__` rather than
# deriving one from `__eq__`, so both rows below disagree with the negation
# of `__eq__`. This is the only position where a union's own `operator!=` is
# reachable -- a container `!=` answers from the elements' `==` in both
# languages (see `union/reference_union_storage_eq`).
class Tagged(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: "Tagged") -> bool:
        if not isinstance(other, Tagged):
            return False
        return self.n == other.n

    def __ne__(self, other: "Tagged") -> bool:
        if not isinstance(other, Tagged):
            return True
        return self.n == other.n


class Marked(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: "Marked") -> bool:
        if not isinstance(other, Marked):
            return False
        return self.n == other.n


def dunder_ne(a: Tagged | Marked, b: Tagged | Marked) -> bool:  # free function
    return a != b  # tpyc: ok


def value_record_lt(a: Fixed | Zone, b: Fixed | Zone) -> str:
    try:
        ordered = a < b  # tpyc: ok
        return "ordered " + str(ordered)
    except TypeError:
        return "TypeError"


# Inverse: every ordering op on an alternative pair Python cannot order
# raises, and an alternative pair Python never calls equal keeps answering
# False. A stable token is printed rather than the message text: TPy's names
# the TPy type where CPython names the Python one
# (BUGS.md#union-order-typeerror-names-tpy-type).
def str_int_cmp(a: int32 | str, b: int32 | str, which: int32) -> str:
    try:
        if which == 0:
            ordered = a < b  # tpyc: ok
        elif which == 1:
            ordered = a <= b  # tpyc: ok
        elif which == 2:
            ordered = a > b  # tpyc: ok
        else:
            ordered = a >= b  # tpyc: ok
        return "ordered " + str(ordered)
    except TypeError:
        return "TypeError"


def str_int_eq(a: int32 | str, b: int32 | str) -> bool:
    return a == b  # tpyc: ok


async def amain(x: int32 | float64, y: int32 | float64) -> None:
    print("async", await in_async(x, y))


def main() -> None:
    x: int32 | float64 = 1
    y: int32 | float64 = 1.0
    print("module", module_eq)
    print("free_fn", free_fn(x, y))
    print("ctor", Holder(x, y).flag)
    print("method", Holder(x, x).method(x, y))
    for v in gen(x, y):
        print("generator", v)
    print("with", in_with(x, y))
    print("closure", in_closure(x, y))
    print("try", in_try(x, y))
    try:
        print("error_return", in_error_return(x, y))
    except Boom:
        print("error_return boom")
    print("match", in_match(x, y))
    asyncio.run(amain(x, y))

    i32: int32 = 1
    f64: float64 = 1.0
    i64: int64 = 1
    u8: uint8 = 1
    u32: uint32 = 1
    flag = True
    big: int = 1
    print("family int32|float64", u_i32_f64(i32, f64), t_i32_f64(i32, f64))
    print("family int32|int64", u_i32_i64(i32, i64), t_i32_i64(i32, i64))
    print("family bool|int32", u_bool_i32(flag, i32), t_bool_i32(flag, i32))
    print("family uint8|int32", u_u8_i32(u8, i32), t_u8_i32(u8, i32))
    print("family uint32|int32", u_u32_i32(u32, i32), t_u32_i32(u32, i32))
    print("family int|float64", u_int_f64(big, f64), t_int_f64(big, f64))
    print("ne", u_ne(x, y))
    print("readonly", u_readonly(x, y))
    print("same alternative", u_i32_f64(i32, i32), u_ne(i32, i32))

    xs: list[int32 | float64] = [1]
    ys: list[int32 | float64] = [1.0]
    print("list", list_eq(xs, ys))
    # The callee's append is visible here, so the list crossed the boundary
    # by reference rather than being copied.
    append_float(xs)
    print("list after append", len(xs), list_eq(xs, ys))
    ts: list[tuple[int32 | float64, int32]] = [(1, 2)]
    us: list[tuple[int32 | float64, int32]] = [(1.0, 2)]
    print("list of tuple", list_of_tuple_eq(ts, us))
    ds: dict[str, int32 | float64] = {"k": 1}
    es: dict[str, int32 | float64] = {"k": 1.0}
    print("dict", dict_eq(ds, es))
    ar: Array[int32 | float64, 2] = [1, 2]
    br: Array[int32 | float64, 2] = [1.0, 2.0]
    print("array", array_eq(ar, br))

    t1: Tree[int32 | float64] = 1
    t2: Tree[int32 | float64] = 1.0
    print("alias", tree_eq(t1, t2))
    vi: list[V] = [1]
    vf: list[V] = [1.0]
    vs: list[V] = ["a"]
    print("nongeneric alias", v_list_eq(vi, vf), v_list_eq(vi, vs))
    # The callee's pop is visible here, so the wrapper list crossed the
    # boundary by reference rather than being copied.
    drop_last_v(vi)
    print("nongeneric alias after pop", len(vi), v_list_eq(vi, vf))
    c1: list[Shape] = [Circle(1)]
    c2: list[Shape] = [Circle(1)]
    sq: list[Shape] = [Square(1)]
    print("record union", shape_list_eq(c1, c2), shape_list_eq(c1, sq))
    # Same boundary check for the record-union container.
    clear_shapes(c1)
    print("record union after clear", len(c1), shape_list_eq(c1, c2))
    dc1: dict[str, Shape] = {"k": Circle(2)}
    dc2: dict[str, Shape] = {"k": Circle(2)}
    dsq: dict[str, Shape] = {"k": Square(2)}
    print("record union dict", shape_dict_eq(dc1, dc2),
          shape_dict_eq(dc1, dsq))
    st1: list[ShapeTree] = [Circle(3)]
    st2: list[ShapeTree] = [Circle(3)]
    st3: list[ShapeTree] = [Square(3)]
    print("record alias", shape_tree_eq(st1, st2), shape_tree_eq(st1, st3))

    print("lt", u_lt(f64, i32), u_lt(i32, 2.5))
    print("le", u_le(i32, f64))
    print("gt", u_gt(i32, 0.5))
    print("ge", u_ge(i32, f64))
    nan = float("nan")
    nan_u: int32 | float64 = nan
    print("nan le", u_le(nan_u, f64), t_le(nan, f64))
    print("nan ge", u_ge(nan_u, f64), t_ge(nan, f64))

    n: int32 | str = 1
    s: int32 | str = "a"
    print("value record lt", value_record_lt(Fixed(1), Fixed(2)))
    print("custom_ne", dunder_ne(Tagged(1), Tagged(1)),
          dunder_ne(Tagged(1), Tagged(2)), dunder_ne(Tagged(1), Marked(1)))
    print("unorderable", str_int_cmp(n, s, 0), str_int_cmp(n, s, 1),
          str_int_cmp(n, s, 2), str_int_cmp(n, s, 3))
    print("str_int_eq", str_int_eq(n, s), str_int_eq(s, s), str_int_eq(n, n))


main()
