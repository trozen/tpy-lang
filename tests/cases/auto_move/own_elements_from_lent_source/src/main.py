# An `Own[T]` loop variable over a source that only LENDS its elements owns a
# COPY (warned at the call), never the caller's element, whatever the body does
# with it; a source at its last use, a fresh container temporary, `own_iter`
# and a multi-input `map` over a dead container hand their elements over. The
# builtin collectors (dict(), set(), extend, slice assignment) likewise move
# in what they are handed and copy what a source lends.
import asyncio
from tpy import int32, Own, nocopy, copy_iter, own_iter
from typing import Iterable, Iterator, Self


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def collect(source: Iterable[Own[list[int32]]]) -> Own[list[list[int32]]]:
    out: list[list[int32]] = []
    for x in source:
        # The subject: `x` owns its value, so this move empties nothing of the caller's.
        out.append(x)
    return out


class Sink:
    rows: list[list[int32]]

    def __init__(self) -> None:
        self.rows = []

    def absorb(self, source: Iterable[Own[list[int32]]]) -> int32:
        for x in source:
            self.rows.append(x)  # method: moves the loop variable's own copy
        return len(self.rows)


def via_closure(source: Iterable[Own[list[int32]]]) -> int32:
    out: list[list[int32]] = []

    def inner() -> None:
        for x in source:
            out.append(x)  # closure: the loop runs inside a nested def

    inner()
    return len(out[0])


def via_try(source: Iterable[Own[list[int32]]]) -> int32:
    out: list[list[int32]] = []
    try:
        for x in source:
            out.append(x)  # try/finally body
    finally:
        out.append([])
    return len(out[0])


def via_match(source: Iterable[Own[list[int32]]], k: int32) -> int32:
    out: list[list[int32]] = []
    match k:
        case 1:
            for x in source:
                out.append(x)  # match arm
        case _:
            pass
    return len(out[0])


def via_comp(source: Iterable[Own[list[int32]]]) -> Own[list[list[int32]]]:
    return [x for x in source]  # comprehension: each element is the loop variable's own copy


def via_gen(source: Iterable[Own[list[int32]]]) -> Iterator[int32]:
    out: list[list[int32]] = []
    for x in source:
        out.append(x)  # generator frame (regression guard)
        yield len(out[-1])


async def via_async(source: Iterable[Own[list[int32]]]) -> int32:
    out: list[list[int32]] = []
    for x in source:
        await asyncio.sleep(0)
        out.append(x)  # coroutine frame (regression guard)
    return len(out[0])


def capture(source: Iterable[Own[list[int32]]]) -> int32:
    total: int32 = 0
    for x in source:
        def size() -> int32:
            return len(x)  # a closure capturing the loop variable reads the variable's own copy
        total += size()
    return total


def unpack_pairs(source: Iterable[Own[tuple[int32, list[int32]]]]) -> int32:
    out: list[tuple[int32, list[int32]]] = []
    # Unpacked in the body, not the loop head: `for k, xs in source` rejects
    # (BUGS.md#own-tuple-iterable-unpack-head-rejects).
    for t in source:
        # A whole tuple element: the loop variable materializes the tuple it owns.
        k, xs = t
        # Copies `xs` out of the loop variable's own tuple, which no one else
        # sees: sema does not count an `Own` loop variable as an owner yet
        # (BUGS.md#own-loop-var-nocopy-move-rejects).
        out.append((k, xs))  # tpyc: warning(/copies list\[int32\] into owned storage/)
    return len(out)


def collect_union(source: Iterable[Own[list[int32] | str]]) -> int32:
    out: list[list[int32] | str] = []
    for u in source:
        out.append(u)  # union element: moves the loop variable's own copy
    return len(out)


def sum_cells(source: Iterable[Own[Cell]]) -> int32:
    total: int32 = 0
    for c in source:
        total += c.n  # read-only: the @nocopy loop variable holds the step's own value
    return total


class Item:
    n: int32
    tags: list[int32]

    def __init__(self, n: int32) -> None:
        self.n = n
        self.tags = [n]

    # Returns itself: reading a scalar field of a consuming `self` crashes
    # (BUGS.md#consuming-method-scalar-field-read-crashes).
    def finish(self: Own[Self]) -> Own[Self]:
        return self


def total(source: Iterable[Own[list[int32]]]) -> int32:
    t: int32 = 0
    for x in source:
        t += len(x)  # a read-only body: x still holds its own copy
    return t


# Each move spelling below moves out of the loop variable's own value, never
# out of the caller's element.
def flatten(source: Iterable[Own[list[int32]]]) -> Own[list[int32]]:
    flat: list[int32] = []
    for x in source:
        flat.extend(x)  # x at its last use is consumed (own_iter)
    return flat


def drop(source: Iterable[Own[list[int32]]]) -> int32:
    n: int32 = 0
    for x in source:
        n += len(x)
        del x  # del sinks the loop variable
    return n


def keep_items(source: Iterable[Own[Item]]) -> int32:
    out: list[Item | None] = []
    for x in source:
        out.append(x)  # moves into an optional element slot
    return len(out)


def finish_all(source: Iterable[Own[Item]]) -> int32:
    t: int32 = 0
    for x in source:
        t += x.finish().n  # a consuming method's receiver
    return t


def keep_opts(source: Iterable[Own[list[int32] | None]]) -> int32:
    out: list[list[int32] | None] = []
    for o in source:
        out.append(o)  # an optional element, moved without narrowing
    return len(out)


def cell_ns(source: Iterable[Own[Cell]]) -> Own[list[int32]]:
    return [c.n for c in source]  # comprehension over owned @nocopy steps: no copy


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __copy__(self) -> Own[Self]:
        print("copy", self.n)
        return C(self.n)


def total_c(source: Iterable[Own[C]]) -> int32:
    t: int32 = 0
    for c in source:
        t += c.n
    return t


def make_cells() -> Own[list[Cell]]:
    out: list[Cell] = []
    out.append(Cell(6))
    out.append(Cell(7))
    return out


def make_cs() -> Own[list[C]]:
    out: list[C] = []
    out.append(C(8))
    return out


def make_pairs() -> Own[list[tuple[str, C]]]:
    out: list[tuple[str, C]] = []
    out.append(("a", C(9)))
    return out


def fresh_cs(k: int32) -> Iterator[Own[C]]:
    yield C(k)
    yield C(k + 1)


class Key:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def __hash__(self) -> int:
        return len(self.name)

    def __eq__(self, o: Self) -> bool:
        return self.name == o.name


def lend_keys(ks: list[Key]) -> Iterator[Key]:
    for k in ks:
        yield k


def keep_c(source: Iterable[Own[C]]) -> Own[list[C]]:
    return [c for c in source]


def first(source: Iterable[Own[list[int32]]]) -> Own[list[int32]]:
    for x in source:
        return x  # return moves the loop variable out
    return []


def take(k: int32, xs: Own[list[int32]]) -> Own[list[int32]]:
    return xs


def take_cell(k: int32, c: Own[Cell]) -> int32:
    return c.n + k


def repeat(xs: list[int32]) -> Iterator[list[int32]]:
    yield xs
    yield xs


def main() -> None:
    ll: list[list[int32]] = [[1, 2]]
    got = collect(ll)  # tpyc: warning(/copies list\[int32\] elements/)
    print("free", len(got[0]), len(ll[0]))
    # copy_iter acknowledges the copy and silences the warning.
    got2 = collect(copy_iter(ll))  # tpyc: ok
    print("free explicit", len(got2[0]), len(ll[0]))
    print("method", Sink().absorb(ll), len(ll[0]))  # tpyc: warning(/copies list\[int32\] elements/)
    print("closure", via_closure(ll), len(ll[0]))  # tpyc: warning(/copies list\[int32\] elements/)
    print("try", via_try(ll), len(ll[0]))  # tpyc: warning(/copies list\[int32\] elements/)
    print("match", via_match(ll, 1), len(ll[0]))  # tpyc: warning(/copies list\[int32\] elements/)
    built = via_comp(ll)  # tpyc: warning(/copies list\[int32\] elements/)
    print("comp", len(built[0]), len(ll[0]))
    for v in via_gen(ll):  # tpyc: warning(/copies list\[int32\] elements/)
        print("gen", v)
    print("gen after", len(ll[0]))
    print("async", asyncio.run(via_async(ll)), len(ll[0]))  # tpyc: warning(/copies list\[int32\] elements/)
    print("capture", capture(ll), len(ll[0]))  # tpyc: warning(/copies list\[int32\] elements/)
    pairs: list[tuple[int32, list[int32]]] = [(0, [1, 2])]
    print("tuple", unpack_pairs(pairs), pairs)  # tpyc: warning(/copies tuple\[int32, list\[int32\]\] elements/)
    us: list[list[int32] | str] = [[1, 2], "a"]
    print("union", collect_union(us), us)  # tpyc: warning(/copies list\[int32\] \| str elements/)
    print("read-only", total(ll), ll)  # tpyc: warning(/copies list\[int32\] elements/)
    print("extend", flatten(ll), ll)  # tpyc: warning(/copies list\[int32\] elements/)
    print("del", drop(ll), ll)  # tpyc: warning(/copies list\[int32\] elements/)
    items: list[Item] = [Item(1), Item(2)]
    # Moving the loop variable would empty the caller's `tags` if it aliased the element.
    print("optional slot", keep_items(items), items[0].n, items[0].tags)  # tpyc: warning(/copies Item elements/)
    print("consuming method", finish_all(items), items[1].n, items[1].tags)  # tpyc: warning(/copies Item elements/)
    opts: list[list[int32] | None] = [[1, 2], None]
    print("optional elements", keep_opts(opts), opts)  # tpyc: warning(/copies list\[int32\] \| None elements/)
    print("return", first(ll), ll)  # tpyc: warning(/copies list\[int32\] elements/)
    # A dead source and an explicit own_iter hand their elements over.
    cells: list[Cell] = [Cell(1), Cell(2)]
    print("dead", sum_cells(cells))  # tpyc: ok
    more: list[Cell] = [Cell(3)]
    print("own_iter", sum_cells(own_iter(more)))  # tpyc: ok
    # An unannotated, unmutated literal local (kept as a fixed array) at its last use moves too.
    literal = [Cell(4), Cell(5)]
    print("literal", sum_cells(literal))  # tpyc: ok
    # A fresh container temporary hands its elements over too (@nocopy: no copy).
    print("rvalue literal", sum_cells([Cell(1), Cell(2)]))  # tpyc: ok
    print("rvalue call", sum_cells(make_cells()))  # tpyc: ok
    # The copy hook would print if a temporary's element were copied.
    print("rvalue hook literal", total_c([C(1), C(2)]))  # tpyc: ok
    print("rvalue hook call", total_c(make_cs()))  # tpyc: ok
    # copy_iter copies each element once (the hook prints in both runtimes),
    # and a write through the copy does not reach the source.
    hooked: list[C] = []
    hooked.append(C(1))
    hooked.append(C(2))
    kept = keep_c(copy_iter(hooked))  # tpyc: ok
    kept[0].n = 50
    print("copy_iter", len(kept), kept[0].n, hooked[0].n)
    # A comprehension over owned @nocopy steps binds each in place.
    cs: list[Cell] = [Cell(1), Cell(2)]
    print("comp own_iter", cell_ns(own_iter(cs)))  # tpyc: ok
    dead_for_comp: list[Cell] = [Cell(3)]
    print("comp dead", cell_ns(dead_for_comp))  # tpyc: ok
    # Multi-input map: a lent element is copied into an Own parameter.
    rows: list[list[int32]] = [[7]]
    for zs in map(take, [0], rows):  # tpyc: warning(/copies list\[int32\] elements/)
        print("map live", len(zs))
    print("map live after", len(rows[0]))
    for zs in map(take, [0], copy_iter(rows)):  # tpyc: ok
        print("map copy_iter", len(zs))
    for v in map(take_cell, [1], [Cell(4)]):  # tpyc: ok
        print("map rvalue", v)
    dead_cells: list[Cell] = [Cell(5)]
    for v in map(take_cell, [1], dead_cells):  # tpyc: ok
        print("map dead", v)
    d: dict[int32, list[int32]] = {0: [7]}
    for zs in map(take, [0], d.values()):  # tpyc: warning(/copies list\[int32\] elements/)
        print("map view", len(zs))
    print("map view after", len(d[0]))
    xs: list[int32] = [7]
    for zs in map(take, [0, 1], repeat(xs)):  # tpyc: warning(/copies list\[int32\] elements/)
        print("map gen", len(zs))
    print("map gen after", len(xs))
    # A comprehension over a generator that lends the caller's list: its
    # elements are copies, so moving them leaves `xs` whole.
    built2 = via_comp(repeat(xs))  # tpyc: warning(/copies list\[int32\] elements/)
    print("comp gen", built2, xs)
    # dict(), extend and slice assignment move in what they are handed: a
    # container handed over and a generator's fresh values (the hook would
    # print on a copy); a lent source keeps its elements.
    by_name = dict(make_pairs())  # tpyc: ok
    print("collect dict", len(by_name), by_name["a"].n)
    grown: list[C] = []
    gen3 = fresh_cs(3)
    grown.extend(gen3)  # tpyc: ok
    grown[0:1] = fresh_cs(5)  # tpyc: ok
    print("collect extend", len(grown), grown[0].n, grown[2].n)
    grown[::2] = fresh_cs(7)  # tpyc: ok
    print("collect stepped", len(grown), grown[0].n, grown[1].n, grown[2].n)
    names = ["r"]
    by_row = dict(zip(names, rows))  # tpyc: warning(/copies tuple\[str, list\[int32\]\] elements/)
    nested: list[list[int32]] = []
    lending = repeat(xs)
    nested.extend(lending)  # tpyc: warning(/copies list\[int32\] elements/)
    print("collect lent", by_row["r"], rows, len(nested), xs)
    # set() over a generator lending the caller's records copies each one.
    ks = [Key("ab")]
    key_set = set(lend_keys(ks))  # tpyc: warning(/copies Key elements/)
    print("collect lent set", len(key_set), ks[0].name)


main()
