# A lazy combinator (enumerate / zip / filter / map / reversed) hands each
# element on in the form its SOURCE steps it: a const container lends const
# elements (so a read-only list parameter iterates), a mutable one lends
# mutable ones, an iterator argument's lent element is lent on, and nothing is
# copied -- a mutation through the loop variable reaches the source, as in
# CPython, through every nesting, in a `for` at every position and in a
# comprehension over a local or a field. A loop variable bound off a
# combinator in a `for` also demotes the parameter the combinator retains
# (through the nesting), so the mutating faces compile against a plain
# `list[T]` parameter; a comprehension over a parameter does not demote it
# (BUGS.md#comprehension-loop-var-mutation-not-propagated), so the
# comprehension sections here iterate locals and fields.
from tpy import int32, Own, Span, readonly, error_return, ReturnException
from typing import Iterator
import asyncio


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def bump(self, d: int32) -> int32:
        self.v += d
        return self.v

    @readonly
    def val(self) -> int32:
        return self.v


class Bag2:
    items: list[int32]

    def __init__(self, n: int32) -> None:
        self.items = [n, n]


def pos(c: Cell) -> bool:
    return c.v > 0


def big(c: Cell) -> bool:
    return c.v > 1


def same(c: Cell) -> Cell:
    return c


def valof(c: Cell) -> int32:
    return c.v


class Failed(Exception, ReturnException):
    pass


def mk() -> Own[list[Cell]]:
    return [Cell(1), Cell(2)]


def each(cells: list[Cell]) -> Iterator[Cell]:
    for c in cells:
        yield c


def show(tag: str, cells: list[Cell]) -> None:
    print(tag, [c.v for c in cells])


# free function: a read-only list parameter is const, and enumerate / zip /
# filter over it lend const elements
def ro_enumerate(cells: list[Cell]) -> int32:
    t = 0
    for i, c in enumerate(cells):  # tpyc: ok
        t += c.v + i
    return t


def ro_zip(cells: list[Cell], ws: list[int32]) -> int32:
    t = 0
    for c, w in zip(cells, ws):  # tpyc: ok
        t += c.v * w
    return t


def ro_filter(cells: list[Cell]) -> int32:
    t = 0
    for c in filter(big, cells):  # tpyc: ok
        t += c.v
    return t


def ro_reversed(cells: list[Cell]) -> int32:
    t = 0
    for c in reversed(cells):  # tpyc: ok
        t = t * 10 + c.v
    return t


# free function: a mutation through the loop variable demotes the parameter
# and reaches the caller's list
def mut_enumerate(cells: list[Cell]) -> int32:
    t = 0
    for i, c in enumerate(cells):  # tpyc: ok
        t += c.bump(i + 1)
    return t


def mut_zip(cells: list[Cell], ws: list[int32]) -> None:
    for c, w in zip(cells, ws):  # tpyc: ok
        c.bump(w)


def mut_filter(cells: list[Cell]) -> None:
    for c in filter(big, cells):  # tpyc: ok
        c.bump(100)


def mut_reversed(cells: list[Cell]) -> None:
    for c in reversed(cells):  # tpyc: ok
        c.bump(1000)


def mut_map(cells: list[Cell]) -> None:
    for c in map(same, cells):  # tpyc: ok
        c.bump(7)


# free function: a NESTED combinator over a parameter climbs through the
# nesting to the parameter
def nested_enumerate_filter(ns: list[Cell]) -> None:
    for i, n in enumerate(filter(pos, ns)):  # tpyc: ok
        n.bump(i + 1)


def nested_zip_reversed(ns: list[Cell], xs: list[int32]) -> None:
    for n, x in zip(reversed(ns), xs):  # tpyc: ok
        n.bump(x)


def nested_map_filter(ns: list[Cell]) -> None:
    for n in map(same, filter(pos, ns)):  # tpyc: ok
        n.bump(10)


def nested_zip_filter(ns: list[Cell], xs: list[int32]) -> None:
    for n, x in zip(filter(pos, ns), xs):  # tpyc: ok
        n.bump(x)


# free function: `map` over a read-only parameter hands its callable the
# const element (a borrow-returning callable takes `Cell&` and cannot be
# mapped over a const list; the const lend is pinned in the runtime
# self-check)
def ro_map(cells: list[Cell]) -> int32:
    t = 0
    for v in map(valof, cells):  # tpyc: ok
        t += v
    return t


# collect: `list(enumerate(..))` / `list(zip(..))` copy the lent element into
# the storage-form tuple and leave the source intact (never move it out)
def collect_copies() -> None:
    bags = [Bag2(1), Bag2(2)]
    pairs = list(enumerate(bags))  # tpyc: ok
    print("collect_copies", len(bags[0].items), len(bags[1].items), len(pairs))
    zs = list(zip(bags, [7, 8]))  # tpyc: ok
    print("collect_copies", len(bags[0].items), len(bags[1].items), len(zs))


# closure: a nested def mutating through a combinator over a captured list
def closure_mut() -> None:
    cells = [Cell(1), Cell(2)]

    def bump_all() -> None:
        for i, c in enumerate(cells):  # tpyc: ok
            c.bump(i + 40)

    bump_all()
    show("closure_mut", cells)


# generator: the frame's loop fields are spelled off the combinator, const
# and mutable alike
def gen_ro(cells: list[Cell]) -> Iterator[int32]:
    for i, c in enumerate(cells):  # tpyc: ok
        yield c.v + i


def gen_mut(cells: list[Cell]) -> Iterator[int32]:
    for i, c in enumerate(cells):  # tpyc: ok
        yield c.bump(i + 10)


def gen_zip_mut(cells: list[Cell], ws: list[int32]) -> Iterator[int32]:
    for c, w in zip(cells, ws):  # tpyc: ok
        yield c.bump(w)


# genexpr: the frame's source parameter is bound off the combinator, and its
# mutation climbs to the caller's parameter
def genexpr_ro(cells: list[Cell]) -> int32:
    return sum(c.v + i for i, c in enumerate(cells))  # tpyc: ok


def genexpr_mut(cells: list[Cell]) -> int32:
    return sum(c.bump(i + 1) for i, c in enumerate(cells))  # tpyc: ok


# comprehension: every nesting and argument kind lends
def comp_shapes() -> None:
    ns = [Cell(1), Cell(2)]
    print("comp_zip_literal", [n.bump(x) for n, x in zip(ns, [10, 20])])  # tpyc: ok
    show("comp_zip_literal", ns)
    print("comp_filter_gen", [n.bump(1) for n in filter(pos, each(ns))])  # tpyc: ok
    show("comp_filter_gen", ns)
    xs = [10, 20]
    print("comp_zip_gen", [n.bump(x) for n, x in zip(each(ns), xs)])  # tpyc: ok
    show("comp_zip_gen", ns)
    print("comp_map_filter", [n.bump(10) for n in map(same, filter(pos, ns))])  # tpyc: ok
    show("comp_map_filter", ns)
    print("comp_reversed", [n.bump(1) for n in reversed(ns)])  # tpyc: ok
    show("comp_reversed", ns)
    print("comp_filter_filter", [n.bump(10) for n in filter(pos, filter(pos, ns))])  # tpyc: ok
    show("comp_filter_filter", ns)
    print("comp_zip_reversed", [n.bump(x) for n, x in zip(reversed(ns), xs)])  # tpyc: ok
    show("comp_zip_reversed", ns)
    print("comp_zip_owned", [n.bump(x.v) for n, x in zip(mk(), ns)])  # tpyc: ok
    show("comp_zip_owned", ns)
    print("comp_enumerate_filter", [n.bump(i) for i, n in enumerate(filter(pos, ns))])  # tpyc: ok
    show("comp_enumerate_filter", ns)
    print("comp_zip_filter", [n.bump(x) for n, x in zip(filter(pos, ns), xs)])  # tpyc: ok
    show("comp_zip_filter", ns)


# method: a const-inferred receiver's field lends const, a mutating one mutable
class Bag:
    cells: list[Cell]

    def __init__(self) -> None:
        self.cells = [Cell(1), Cell(2)]

    def total(self) -> int32:
        t = 0
        for i, c in enumerate(self.cells):  # tpyc: ok
            t += c.v + i
        return t

    def bump_all(self) -> None:
        for i, c in enumerate(self.cells):  # tpyc: ok
            c.bump(i + 5)

    # comprehension over a FIELD under a const-inferred receiver: the field
    # arrives const and the combinator lends const
    def comp_total(self, ws: list[int32]) -> int32:
        return sum([c.v * w for c, w in zip(self.cells, ws)])  # tpyc: ok


# async: the same frame, suspending inside the loop
async def async_mut(cells: list[Cell]) -> int32:
    t = 0
    for i, c in enumerate(cells):  # tpyc: ok
        await asyncio.sleep(0)
        t += c.bump(i + 20)
    return t


# explicit readonly element: a `Span[readonly[Cell]]` lends const elements
# through a combinator, in a comprehension too; a @readonly method call on
# the const element is fine
def explicit_ro(cells: Span[readonly[Cell]], ws: list[int32]) -> int32:
    t = 0
    for i, c in enumerate(cells):  # tpyc: ok
        t += c.val() * (i + 1)
    return t + sum([c.val() + w for c, w in zip(cells, ws)])  # tpyc: ok


# value elements are carried by value: an int and a str element read as
# values, and a write to the loop variable does not reach the list
def values(names: list[str], ns: list[int32]) -> None:
    for i, s in enumerate(names):  # tpyc: ok
        print("values", i, s)
    for n, s in zip(ns, names):  # tpyc: ok
        n += 1
        print("values", n, s)
    print("values", ns)


# @error_return body and match arm: the same loop head
@error_return(Failed)
def er_body(cells: list[Cell]) -> int32:
    t = 0
    for i, c in enumerate(cells):  # tpyc: ok
        t += c.bump(i)
    if t < 0:
        raise Failed()
    return t


def match_arm(cells: list[Cell], k: int32) -> int32:
    match k:
        case 0:
            return sum(c.v for c in cells)
        case _:
            t = 0
            for c, w in zip(cells, [k, k]):  # tpyc: ok
                t += c.bump(w)
            return t


def main() -> None:
    cells = [Cell(1), Cell(2)]
    print("ro_enumerate", ro_enumerate(cells))
    print("ro_zip", ro_zip(cells, [3, 4]))
    print("ro_filter", ro_filter(cells))
    print("ro_reversed", ro_reversed(cells))
    print("mut_enumerate", mut_enumerate(cells))
    show("mut_enumerate", cells)
    mut_zip(cells, [10, 20])
    show("mut_zip", cells)
    mut_filter(cells)
    show("mut_filter", cells)
    mut_reversed(cells)
    show("mut_reversed", cells)
    mut_map(cells)
    show("mut_map", cells)
    print("ro_map", ro_map(cells))

    cells = [Cell(1), Cell(2)]
    nested_enumerate_filter(cells)
    nested_zip_reversed(cells, [10, 20])
    nested_map_filter(cells)
    nested_zip_filter(cells, [100, 200])
    show("nested_param", cells)
    collect_copies()
    closure_mut()

    cells = [Cell(1), Cell(2)]
    print("gen_ro", list(gen_ro(cells)))
    print("gen_mut", list(gen_mut(cells)))
    show("gen_mut", cells)
    print("gen_zip_mut", list(gen_zip_mut(cells, [1, 2])))
    show("gen_zip_mut", cells)
    print("genexpr_ro", genexpr_ro(cells))
    print("genexpr_mut", genexpr_mut(cells))
    show("genexpr_mut", cells)

    comp_shapes()

    bag = Bag()
    print("method_total", bag.total())
    bag.bump_all()
    show("method_bump", bag.cells)
    print("comp_total", bag.comp_total([1, 2]))

    cells = [Cell(1), Cell(2)]
    print("async_mut", asyncio.run(async_mut(cells)))
    show("async_mut", cells)
    print("explicit_ro", explicit_ro(cells, [10, 20]))
    values(["a", "bb"], [1, 2])

    cells = [Cell(1), Cell(2)]
    try:
        print("er_body", er_body(cells))
    except Failed:
        print("er_body failed")
    show("er_body", cells)
    print("match_arm", match_arm(cells, 3), match_arm(cells, 0))
    show("match_arm", cells)


main()
