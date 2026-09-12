# A generic body that stores an open `T` into an owning slot warns at its own
# line, whatever anything instantiates it at -- the copy contract a library
# author reads without a call site. `copy()` and a `T: ValueType` bound are
# the only silencers; a non-copyable instantiation (`@nocopy`, or a record
# with `__del__`) turns the line into an error -- that leg is
# `error_generic_own_copy_noncopyable_instantiation`.
import asyncio
from typing import Iterator

from gencontainer import cross_slot
from tpy import Copyable, int32, Own, ValueType, copy, error_return, readonly, ReturnException


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Tag:
    t: int32

    def __init__(self, t: int32) -> None:
        self.t = t


class Missing(Exception, ReturnException):
    pass


# free function, container-element slot. `diag.txt` is the subject of every
# section: nothing for the value instantiation, `copies X into <sink>` for the
# reference one, on the BODY line rather than at the call that decided it.
def free_slot[T](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)


def free_slot_twin(v: Cell) -> int32:
    xs: list[Cell] = []
    xs.append(v)  # tpyc: warning(/copies Cell into owned storage/)
    return len(xs)


# free function, `Own[T]` return slot
def ret_own[T](v: T) -> Own[T]:
    return v  # tpyc: warning(/may copy T into owned storage/)


def ret_own_twin(v: Cell) -> Own[Cell]:
    return v  # tpyc: warning(/copies Cell into owned storage/)


# constructor and method, field slot
class Holder[T]:
    item: T

    def __init__(self, v: T) -> None:
        self.item = v  # tpyc: warning(/may copy T into field/)

    def store(self, v: T) -> None:
        self.item = v  # tpyc: warning(/may copy T into field/)


class HolderTwin:
    item: Cell

    def __init__(self, v: Cell) -> None:
        self.item = v  # tpyc: warning(/copies Cell into field/)

    def store(self, v: Cell) -> None:
        self.item = v  # tpyc: warning(/copies Cell into field/)


# two distinct reference instantiations of ONE body: still ONE line, because
# the contract is the declaration's and not any call's
def two_instantiations[T](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)


# generator body
def gen_slot[T](v: T) -> Iterator[int32]:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    yield len(xs)


# async body
async def async_slot[T](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)


async def async_driver(c: Cell) -> int32:
    ref = await async_slot(c)
    val = await async_slot(4)
    return ref + val


# closure body
def closure_slot[T](v: T) -> int32:
    xs: list[T] = []

    def inner() -> None:
        xs.append(v)  # tpyc: warning(/may copy T into owned storage/)

    inner()
    return len(xs)


class Guard:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# context-manager body
def with_slot[T](v: T) -> int32:
    xs: list[T] = []
    with Guard() as g:
        xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs) + g


# try / finally body
def try_slot[T](v: T) -> int32:
    xs: list[T] = []
    try:
        xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    finally:
        xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)


# match arm
def match_slot[T](v: T, tag: int32) -> int32:
    xs: list[T] = []
    match tag:
        case 1:
            xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
        case _:
            pass
    return len(xs)


# @error_return body
@error_return(Missing)
def er_slot[T](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)


# transitive forward: only `outer` knows U, only `inner` carries the obligation
def inner_fwd[T](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)


def outer_fwd[U](v: U) -> int32:
    return inner_fwd(v)  # tpyc: ok


# generic base declaration: `GenBase[Cell]` is named only through the header
class GenBase[T]:
    item: T

    def __init__(self, v: T) -> None:
        self.item = v  # tpyc: warning(/may copy T into field/)


class GenMid[T](GenBase[T]):
    def __init__(self, v: T) -> None:
        super().__init__(v)  # tpyc: ok


class GenLeaf(GenMid[Cell]):  # tpyc: ok
    def __init__(self, v: Cell) -> None:
        super().__init__(v)  # tpyc: ok


# composite payload: `T | None` contains a param, so it hedges like a bare one
class OptHolder[T]:
    item: T | None

    def __init__(self) -> None:
        self.item = None

    def store(self, v: T | None) -> None:
        self.item = v  # tpyc: warning(/may copy T \| None into field/)


# SILENCER 1: `T: ValueType` -- a reference-type copy cannot happen there, so
# there is nothing to declare. (A `T | None` field under the same bound would
# say the same thing, but its member write is a lowering reject.)
def value_bound[T: ValueType](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: ok
    return len(xs)


# ... and it reaches through the shapes whose value-ness DELEGATES to the
# param: `readonly[T]` over a value-bound `T` is still a value. (`T | None`
# and `tuple[.., T]` delegate the same way -- probed silent -- but neither
# lowers at an owning slot: a `T | None` field write rejects at
# `assign.field_write_shape` and a `tuple[.., T]` one at
# `ctor.mil_field.tuple.name`, so the case pins the shape that does.)
def value_bound_readonly[T: ValueType](v: readonly[T]) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: ok
    return len(xs)


# ... but NOT through a payload that is a reference type in its own right.
# `GBox[T]` is a struct that gets copied whatever `T` is, so the bound settles
# nothing and the hedge stands -- beside the twin, which says so concretely.
class GBox[T]:
    item: T

    def __init__(self, v: T) -> None:
        self.item = v  # tpyc: warning(/may copy T into field/)


def boxed_bound[T: ValueType](v: GBox[T]) -> int32:
    xs: list[GBox[T]] = []
    xs.append(v)  # tpyc: warning(/may copy GBox\[T\] into owned storage/)
    return len(xs)


def boxed_bound_twin(v: GBox[int32]) -> int32:
    xs: list[GBox[int32]] = []
    xs.append(v)  # tpyc: warning(/copies GBox\[int32\] into owned storage/)
    return len(xs)


# The same question at DEPTH -- `GBox[T] | None` must still hedge and
# `tuple[T, int32] | None` must stay silent -- is pinned in
# tpyc/test_compiler.py instead: every owning sink for a nested payload is a
# lowering reject today (`method.arg_shape` at `.append`,
# `call.generic_arg_slot` at an `Own[T]` argument, `return.slot_type` /
# `return.tuple_source` at a return, `foreach.elem_family.optional` at a
# `yield` consumer, `call.ctor_arg.optional` at a field), so a section here
# would fail to compile rather than report.


# a METHOD-level bound shadows the class-level one, and the silencer reads the
# method's: `keep` is silent under its own `T: ValueType` while the ctor next
# to it, whose `T` is only `Copyable`, still hedges. (A shadowed name
# constrains the CLASS param rather than opening a fresh one, so the receiver
# must be instantiated at a value type for `keep` to be callable at all.)
class Shadowed[T: Copyable]:
    item: T

    def __init__(self, v: T) -> None:
        self.item = v  # tpyc: warning(/may copy T into field/)

    def keep[T: ValueType](self, v: T) -> int32:
        xs: list[T] = []
        xs.append(v)  # tpyc: ok
        return len(xs)


# container-conversion ELEMENTS, tuple-nested: `tuple[str, T]` is a value type
# whose T is not, so the copy question has to be asked recursively -- the
# whole-shape `is_value_type()` would call this silent.
def elems_ctor[T](pairs: list[tuple[str, T]]) -> int32:
    d = dict(pairs)  # tpyc: warning(/may copy tuple\[str, T\] elements/)
    return len(d)


def elems_ctor_twin(pairs: list[tuple[str, Cell]]) -> int32:
    d = dict(pairs)  # tpyc: warning(/copies tuple\[str, Cell\] elements/)
    return len(d)


# the same element sink at the other three spellings
def elems_list[T](xs: list[T]) -> int32:
    ys = list(xs)  # tpyc: warning(/may copy T elements/)
    return len(ys)


def elems_iadd[T](xs: list[T], ys: list[T]) -> int32:
    xs += ys  # tpyc: warning(/may copy T elements/)
    return len(xs)


def elems_update[T](a: dict[str, T], b: dict[str, T]) -> int32:
    a.update(b)  # tpyc: warning(/may copy T elements/)
    return len(a)


# comprehension body, TWIN ONLY: a comprehension whose element is an open `T`
# records its obligation but cannot be emitted at all -- `expr.list_comp` is a
# pre-existing lowering reject queued in scripts/thir_migration/review, and a
# set comprehension there is refused earlier still (`Ref[T]` is not hashable).
# The concrete side is pinned so the position is not silently uncovered.
def comp_slot_twin(v: Cell) -> int32:
    xs: list[Cell] = [v for _ in range(2)]  # tpyc: warning(/copies Cell into owned storage/)
    return len(xs)


# subscript assign: the container dest, distinct from the field one
def subscript_slot[T](xs: list[T], v: T) -> int32:
    xs[0] = v  # tpyc: warning(/may copy T into container/)
    return len(xs)


def subscript_slot_twin(xs: list[Cell], v: Cell) -> int32:
    xs[0] = v  # tpyc: warning(/copies Cell into container/)
    return len(xs)


# instantiated ONLY at a value type: the contract is the body's, so the line
# stands -- this is the accepted divergence from the monomorphic twin, which
# would report nothing at all here
def value_only[T](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)


# never instantiated: the point of a DECLARATION-time contract -- a library
# generic warns its author with no call site anywhere
def never_used[T](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)


# `T: Copyable` does NOT silence: copyable is TPy's default, so the bound only
# rules out a non-copyable payload -- it does not say a copy was intended
def bounded_copyable[T: Copyable](v: T) -> int32:
    xs: list[T] = []
    xs.append(v)  # tpyc: warning(/may copy T into owned storage/)
    return len(xs)


# SILENCER 2: `copy()` at the slot is the author saying the copy is intended
def hatched[T](v: T) -> int32:
    xs: list[T] = []
    xs.append(copy(v))  # tpyc: ok
    return len(xs)


def free_positions() -> None:
    c = Cell(1)
    print("free_slot", free_slot(c), free_slot_twin(c), free_slot(7))
    print("ret_own", ret_own(c).n, ret_own_twin(c).n, ret_own(7))


def record_positions() -> None:
    c = Cell(2)
    h = Holder(c)
    h.store(c)
    hv = Holder(5)
    hv.store(6)
    tw = HolderTwin(c)
    tw.store(c)
    print("holder", h.item.n, hv.item, tw.item.n)
    leaf = GenLeaf(c)
    print("genleaf", leaf.item.n)


def body_positions() -> None:
    c = Cell(3)
    total = 0
    for v in gen_slot(c):
        total += v
    for v in gen_slot(4):
        total += v
    print("gen", total)
    print("async", asyncio.run(async_driver(c)))
    print("closure", closure_slot(c), closure_slot(4))
    print("with", with_slot(c), with_slot(4))
    print("try", try_slot(c), try_slot(4))
    print("match", match_slot(c, 1), match_slot(4, 1))
    try:
        got = er_slot(c)
    except Missing:
        got = -1
    try:
        got_i = er_slot(4)
    except Missing:
        got_i = -1
    print("error_return", got, got_i)


def other_positions() -> None:
    c = Cell(5)
    print("forward", outer_fwd(c), outer_fwd(6))
    print("two_instantiations", two_instantiations(c), two_instantiations(Tag(1)), two_instantiations(7))
    print("cross_module", cross_slot(c), cross_slot(8))
    oh = OptHolder[Cell]()
    oh.store(c)
    print("optional", oh.item.n if oh.item is not None else 0,
          value_bound(9), value_bound_readonly(8))
    gb = GBox(1)
    sh = Shadowed(7)
    print("bound", boxed_bound(gb), boxed_bound_twin(gb), sh.keep(5))
    print("inverse", value_only(1), bounded_copyable(c), hatched(c),
          hatched(2))


def element_positions() -> None:
    # Every fixture element is a FRESH rvalue, which constructs into the
    # container instead of copying, so each line diag.txt reports below
    # belongs to a section above rather than to this setup.
    c = Cell(6)
    pairs = [("a", Cell(1))]
    vpairs = [("a", 1)]
    print("elems_ctor", elems_ctor(pairs), elems_ctor_twin(pairs),
          elems_ctor(vpairs))
    cells = [Cell(2)]
    ints = [1]
    print("elems_list", elems_list(cells), elems_list(ints))
    more = [Cell(3)]
    vmore = [1]
    print("elems_iadd", elems_iadd(cells, more), elems_iadd(ints, vmore))
    cmap = {"k": Cell(4)}
    vmap = {"k": 1}
    print("elems_update", elems_update(cmap, cmap), elems_update(vmap, vmap))
    print("comp", comp_slot_twin(c))
    print("subscript", subscript_slot(cells, c), subscript_slot_twin(cells, c),
          subscript_slot(ints, 7))


def main() -> None:
    free_positions()
    record_positions()
    body_positions()
    other_positions()
    element_positions()


main()
