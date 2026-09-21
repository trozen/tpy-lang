# A `for` inside a resumable frame evaluates a CONTAINER iterable exactly ONCE:
# an rvalue source is kept in the frame for the loop's whole life, an lvalue
# source is captured by reference exactly as in a plain function. (An
# iterator-OBJECT source is re-rendered per advance --
# BUGS.md#frame-iter-next-source-reevaluated -- so no section claims otherwise
# for one.)
#
# Every section's loop body SUSPENDS. The lvalue sections mutate an element
# through the loop variable and the caller reads the change back, so a copy
# would be visible; the rvalue sections allocate between the yields, so a
# holder that died at the end of its state block would be disturbed. The
# accessor sections count their calls -- one, as CPython counts them.
from typing import Iterator
from tpy import int32, Own, copy
import asyncio


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Bag:
    cells: list[Cell]
    calls: int32

    def __init__(self) -> None:
        self.cells = [Cell(1), Cell(2)]
        self.calls = 0

    def items(self) -> list[Cell]:
        self.calls += 1
        return self.cells

    # A getter is readonly, so this section cannot count its calls; it
    # carries the property spelling of the lvalue capture instead.
    @property
    def view(self) -> list[Cell]:
        return self.cells

    def walk(self) -> Iterator[str]:
        # self.field source -- an lvalue, captured by reference.
        for c in self.cells:  # tpyc: ok
            c.v += 100
            yield "field " + str(c.v)


def churn() -> int32:
    junk: list[list[int32]] = []
    for i in range(40):
        junk.append([i, i + 1, i + 2])
    return len(junk)


def make_cells() -> Own[list[Cell]]:
    return [Cell(10), Cell(20)]


def gen_cells() -> Iterator[Own[Cell]]:
    yield Cell(7)
    yield Cell(8)


def named_src() -> Iterator[str]:
    cells = [Cell(1), Cell(2)]
    # named local source -- an lvalue.
    for c in cells:  # tpyc: ok
        c.v += 100
        yield "named " + str(c.v)
    yield "named total " + str(cells[0].v + cells[1].v)


def param_src(cells: list[Cell]) -> Iterator[str]:
    # param source -- an lvalue; the caller sees the mutation.
    for c in cells:  # tpyc: ok
        c.v += 100
        yield "param " + str(c.v)


def subscript_src(table: dict[str, list[Cell]]) -> Iterator[str]:
    # container-ELEMENT subscript source -- an lvalue (one `__getitem__`).
    for c in table["a"]:  # tpyc: ok
        c.v += 100
        yield "subscript " + str(c.v)


def accessor_src(b: Bag) -> Iterator[str]:
    # impure borrow-returning method -- an lvalue, called once.
    for c in b.items():  # tpyc: ok
        c.v += 100
        yield "accessor " + str(c.v)


def property_src(b: Bag) -> Iterator[str]:
    # `@property` read -- the accessor spelling of the same lvalue.
    for c in b.view:  # tpyc: ok
        c.v += 100
        yield "property " + str(c.v)


def own_call_src() -> Iterator[str]:
    # `Own[list]` call -- an rvalue the frame owns.
    for c in make_cells():  # tpyc: ok
        yield "own_call " + str(c.v)
        yield "own_call churn " + str(churn())


def copy_src(cells: list[Cell]) -> Iterator[str]:
    # `copy(...)` -- an rvalue; mutating it must NOT reach the caller's list.
    for c in copy(cells):  # tpyc: ok
        c.v += 100
        yield "copy " + str(c.v)
        yield "copy churn " + str(churn())


def literal_src() -> Iterator[str]:
    # container literal -- an rvalue spelled with the slot's own type.
    for c in [Cell(4), Cell(5)]:  # tpyc: ok
        yield "literal " + str(c.v)
        yield "literal churn " + str(churn())


def slice_mut_src() -> Iterator[str]:
    cells = [Cell(1), Cell(2), Cell(3)]
    # slice of a MUTABLE source -- an rvalue view the frame holds.
    for c in cells[1:]:  # tpyc: ok
        c.v += 100
        yield "slice_mut " + str(c.v)
    yield "slice_mut total " + str(cells[1].v + cells[2].v)


def slice_const_src(cells: list[Cell]) -> Iterator[str]:
    # slice of a CONST source -- the slot type is deduced, not named.
    for c in cells[1:]:  # tpyc: ok
        yield "slice_const " + str(c.v)
        yield "slice_const churn " + str(churn())


def ternary_lvalues_src(flag: bool) -> Iterator[str]:
    xs = [Cell(1)]
    ys = [Cell(2)]
    # ternary of two LVALUES -- still an lvalue, captured by reference.
    for c in (xs if flag else ys):  # tpyc: ok
        c.v += 100
        yield "ternary_lv " + str(c.v)
    yield "ternary_lv total " + str(xs[0].v)


def ternary_temps_src(flag: bool) -> Iterator[str]:
    # ternary of two TEMPORARIES -- an rvalue; both arms are fresh lists.
    for c in ([Cell(10)] if flag else [Cell(11)]):  # tpyc: ok
        yield "ternary_tmp " + str(c.v)
        yield "ternary_tmp churn " + str(churn())


def walrus_src() -> Iterator[str]:
    # a WALRUS source denotes its target, so the loop aliases what `ws` holds
    # and a mutation through the loop var is visible through `ws` afterwards.
    for c in (ws := make_cells()):  # tpyc: ok
        c.v += 100
        yield "walrus " + str(c.v)
        yield "walrus churn " + str(churn())
    yield "walrus target " + str(ws[0].v + ws[1].v)


def str_temp_src(a: str, b: str) -> Iterator[str]:
    # a `str` temporary iterated by char -- an rvalue with value elements.
    for ch in a + b:  # tpyc: ok
        yield "str_tmp " + ch


def gen_call_src() -> Iterator[str]:
    # a generator CALL -- an rvalue frame embedded in this one.
    for c in gen_cells():  # tpyc: ok
        yield "gen_call " + str(c.v)
        yield "gen_call churn " + str(churn())


async def async_accessor(b: Bag) -> int32:
    total = 0
    # the async twin of the impure accessor -- one call.
    for c in b.items():  # tpyc: ok
        c.v += 100
        total += c.v
        await asyncio.sleep(0)
    return total


async def async_slice(cells: list[Cell]) -> int32:
    total = 0
    # the async twin of the const-source slice.
    for c in cells[1:]:  # tpyc: ok
        total += c.v
        await asyncio.sleep(0)
    return total


def gx_named() -> int32:
    xs = [1, 2, 3]
    # genexpr frame over a named source -- the constructor seeds from it.
    return sum(v * 2 for v in xs)  # tpyc: ok


def gx_own_call() -> int32:
    # genexpr frame over an `Own[list]` call -- the frame owns the source.
    return sum(c.v for c in make_cells())  # tpyc: ok


def gx_accessor(b: Bag) -> int32:
    # genexpr frame over an impure accessor -- called once.
    return sum(c.v for c in b.items())  # tpyc: ok


def drain(it: Iterator[str]) -> None:
    for s in it:
        print(s)


def main() -> None:
    drain(named_src())

    pcells = [Cell(1), Cell(2)]
    drain(param_src(pcells))
    print("param after", pcells[0].v, pcells[1].v)

    b0 = Bag()
    for s in b0.walk():
        print(s)
    print("field after", b0.cells[0].v, b0.cells[1].v)

    table: dict[str, list[Cell]] = {"a": [Cell(1), Cell(2)]}
    drain(subscript_src(table))
    print("subscript after", table["a"][0].v, table["a"][1].v)

    b1 = Bag()
    drain(accessor_src(b1))
    print("accessor after", b1.cells[0].v, "calls", b1.calls)

    b2 = Bag()
    drain(property_src(b2))
    print("property after", b2.cells[0].v, b2.cells[1].v)

    drain(own_call_src())

    ccells = [Cell(1), Cell(2)]
    drain(copy_src(ccells))
    print("copy after", ccells[0].v, ccells[1].v)

    drain(literal_src())
    drain(slice_mut_src())
    drain(slice_const_src([Cell(1), Cell(2), Cell(3)]))
    drain(ternary_lvalues_src(True))
    drain(ternary_temps_src(True))
    drain(walrus_src())
    drain(str_temp_src("ab", "c"))
    drain(gen_call_src())

    b3 = Bag()
    print("async_accessor", asyncio.run(async_accessor(b3)),
          "calls", b3.calls)
    print("async_slice", asyncio.run(async_slice([Cell(1), Cell(2), Cell(3)])))

    print("gx_named", gx_named())
    print("gx_own_call", gx_own_call())
    b4 = Bag()
    print("gx_accessor", gx_accessor(b4), "calls", b4.calls)


main()
