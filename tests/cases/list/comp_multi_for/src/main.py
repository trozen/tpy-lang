# Comprehensions and generator expressions with several `for` clauses, at
# every position, over every source family and range step.
import asyncio
from typing import Callable, Iterable, Iterator
from tpy import Own, Ptr, int32, int64, nocopy, error_return, ReturnException


class C:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def bump(self) -> int:
        self.n += 1
        return self.n


class Grid:
    rows: list[list[int]]
    extras: list[int]

    def __init__(self) -> None:
        self.rows = [[1, 2], [3]]
        self.extras = [100, 200]

    def total(self) -> int:
        # method: clause 0 is a field, clause 1 iterates the outer var
        return sum(x for row in self.rows for x in row)  # tpyc: ok

    def crossed(self) -> Own[list[int]]:
        # method: an inner clause iterates a field of the captured `self`
        return list(x + e for row in self.rows for x in row if x != 2 for e in self.extras)  # tpyc: ok

    def crossed_list(self) -> Own[list[int]]:
        # method: a list comprehension whose inner clause iterates a field of `self`
        return [x + e for row in self.rows for x in row if x != 2 for e in self.extras]  # tpyc: ok


class Holder:
    items: list[int]

    def __init__(self) -> None:
        self.items = [10, 20]


class CPtr:
    p: Ptr[C]

    def __init__(self, p: Ptr[C]) -> None:
        self.p = p


def pick(cs: list[C]) -> list[C]:
    return cs


def relay(it: Iterable[int32]) -> Iterator[int32]:
    for v in it:
        yield v + 100


class GenCtor:
    total: int

    def __init__(self, rows: list[list[int]]) -> None:
        self.total = 0
        # constructor body: a two-clause genexpr
        self.total = sum(x for row in rows for x in row)  # tpyc: ok


def free(rows: list[list[int]], words: list[str], tagged: list[tuple[int, str]]) -> None:
    # free function: consumed by sum, filters at both levels
    print("free sum", sum(x for row in rows if len(row) > 1 for x in row if x > 1))  # tpyc: ok
    # free function: consumed by list(), the inner iterable is built from the outer var
    print("free list", list(a * 10 + b for a in range(3) for b in range(a)))  # tpyc: ok
    # free function: consumed by a for loop, a str flattened
    for c in (c for w in words for c in w):  # tpyc: ok
        print("free char", c)
    # free function: tuple unpack in the inner clause
    print("free unpack", list(f"{s}{n * k}" for k in range(1, 3) for n, s in tagged if n >= k))  # tpyc: ok
    # free function: `_` bound by two clauses is a throwaway, not a reuse
    print("free underscore", sum(1 for _ in range(2) for _ in range(3)))  # tpyc: ok


def bump_all(grid: list[list[C]]) -> int:
    # mutation through the inner var reaches the records in `grid`
    return sum(c.bump() for row in grid for c in row)  # tpyc: ok


def bump_nested() -> None:
    grid = [[C(1), C(2)], [C(3)]]
    # mutated-loop-var edge: the inner comprehension mutates `c`, so the outer
    # comprehension's `row` binds mutably
    out = [[c.bump() for c in row] for row in grid]  # tpyc: ok
    print("bump nested", out)
    # observed after the boundary: the records in `grid` were bumped in place
    print("bump nested seen", grid[0][0].n, grid[0][1].n, grid[1][0].n)


def gen(rows: list[list[int]]) -> Iterator[int]:
    # generator body: a two-clause genexpr feeding the generator's own loop
    for v in (x + 1 for row in rows for x in row if x > 1):  # tpyc: ok
        yield v


async def work(rows: list[list[int]]) -> int:
    await asyncio.sleep(0)
    # async def: a two-clause genexpr after a suspension
    return sum(x * 2 for row in rows for x in row)  # tpyc: ok


def captured() -> None:
    # annotated for the nested def below
    # (BUGS.md#nested-def-tuple-of-captured-pending-list-ice)
    a_list: list[int] = [1, 2]
    b_list: list[int] = [10, 20]
    # inner clause iterating a capture: it is held for the frame's lifetime
    # like the source, so growing it in the loop the genexpr feeds warns
    for v in (a + b for a in a_list for b in b_list):  # tpyc: ok
        if v > 1000:
            b_list.append(v)  # tpyc: warning(/Mutation of 'b_list' while iterating/)
        print("captured", v)

    def inner() -> int:
        # nested def: both clauses read the enclosing function's lists
        return sum(a * b for a in a_list for b in b_list)  # tpyc: ok
    print("nested def", inner())


def captured_places(limit: int) -> None:
    a = [1, 2]
    h = Holder()
    # an inner clause iterating a FIELD of a capture: growing that field in
    # the loop the genexpr feeds warns like the single-clause genexpr does
    for v in (x + y for x in a for y in h.items):  # tpyc: ok
        if v > limit:
            h.items.append(v)  # tpyc: warning(/Mutation of 'h.items' while iterating/)
        print("place field", v)
    bs: list[list[int32]] = [[10, 20]]
    # an inner clause iterating an ELEMENT of a capture
    for w in (x + y for x in a for y in bs[0]):  # tpyc: ok
        if w > limit:
            bs[0].append(w)  # tpyc: warning(/Mutation of 'bs\[\.\.\.\]' while iterating/)
        print("place element", w)
    ix = [0]
    # an inner clause iterating an element an OUTER clause's target indexes:
    # `x` is 0, so `bs[x]` is `bs[0]` and the append below grows the list
    # being iterated -- the "may hit" warning is right
    for u in (x + y for x in ix for y in bs[x]):  # tpyc: ok
        if u > limit:
            bs[0].append(u)  # tpyc: warning(/Mutation of 'bs\[\.\.\.\]' may hit the element being iterated/)
        print("place target index", u)


def kept(xs: list[int32]) -> None:
    b_list: list[int32] = [10, 20]
    # kept past the statement by a lazy consumer bound to a name; the capture
    # an inner clause iterates is rebound only after the last read of `g`
    g = relay(a + b for a in xs for b in b_list)  # tpyc: ok
    for v in g:
        print("kept", v)
    b_list = [5]
    print("kept rebound after", b_list)


def genexpr_positions(rows: list[list[int]]) -> None:
    print("genexpr ctor", GenCtor(rows).total)
    try:
        # try/finally body
        print("genexpr try", sum(x for row in rows for x in row if x > 1))  # tpyc: ok
    finally:
        print("genexpr finally")
    # lambda body
    f: Callable[[list[list[int]]], int] = lambda rs: sum(x for r in rs for x in r)  # tpyc: ok
    print("genexpr lambda", f(rows))
    # another comprehension's element: a multi-clause genexpr and comprehension
    print("genexpr in elem", [sum(x * k for r in rows for x in r) for k in range(1, 3)])  # tpyc: ok
    print("comp in elem", [[x + k for r in rows for x in r] for k in range(2)])  # tpyc: ok
    # a nested genexpr in a filter binds its own `x`; the later clause's `x`
    # is a different variable, so nothing is read before it is bound
    print("filter genexpr", [x for row in rows if any(x > 1 for x in row) for x in row])  # tpyc: ok


@nocopy
class W:
    id: int

    def __init__(self, i: int) -> None:
        self.id = i


def widgets(n: int) -> Iterator[Own[W]]:
    i = 0
    while i < n:
        yield W(i)
        i += 1


def cells(n: int) -> Iterator[Own[C]]:
    i = 0
    while i < n:
        yield C(i)
        i += 1


def keep(row: list[int]) -> bool:
    print("lc keep", len(row))
    return len(row) != 2


def inner_src(row: list[int]) -> Own[list[int]]:
    print("lc inner_src", len(row))
    return [v for v in row]


def lc_free(grid: list[list[int]], words: list[str], ps: list[tuple[int, int]],
            tagged: list[tuple[int, str]]) -> None:
    # free function: scalar flatten
    print("lc flat", [x for row in grid for x in row])  # tpyc: ok
    # free function: an inner source is evaluated once per outer element that
    # passed the outer filter, and never for one that failed it
    kept = [x for row in grid if keep(row) for x in inner_src(row) if x > 1]  # tpyc: ok
    print("lc filtered", kept)
    # free function: set comprehension over two clauses
    rems = {x % 3 for row in grid for x in row}  # tpyc: ok
    print("lc set", sorted(rems))
    # free function: dict comprehension, tuple unpack in the inner clause
    print("lc dict unpack", {s: n * k for k in range(1, 3) for n, s in tagged if n >= k})  # tpyc: ok
    # free function: duplicate keys across clauses -- the last value wins, the
    # first insertion fixes the order
    print("lc dict dup", {x % 3: x for row in grid for x in row})  # tpyc: ok
    # free function: inner range bound read from the outer var
    print("lc range", [i * 10 + j for i in range(4) for j in range(i)])  # tpyc: ok
    # free function: three clauses
    print("lc three", [i * 100 + j * 10 + k for i in range(2) for j in range(2) for k in range(i + j + 1)])  # tpyc: ok
    # free function: str flattening
    chars = [c for w in words for c in w]  # tpyc: ok
    for c in chars:
        print("lc char", c)
    # free function: tuple unpack in both clauses
    print("lc unpack both", [a * b + p * q for a, b in ps for p, q in ps if a != p])  # tpyc: ok
    # free function: `_` bound by two clauses that nothing reads is a throwaway
    print("lc underscore", [0 for _ in grid for _ in words])  # tpyc: ok
    # free function: empty inner sources contribute nothing
    empties: list[list[int]] = [[], [7], []]
    print("lc empty", [x for row in empties for x in row])  # tpyc: ok
    # free function: an inner-filter walrus binds in the enclosing scope
    big = [y for row in grid if len(row) == 1 for y in range(row[0]) if (last := y * 2) > 0]  # tpyc: ok
    print("lc walrus", big, last)
    # free function: an outer-filter walrus is read by the inner source
    spans = [y for row in grid if (m := len(row)) > 1 for y in range(m)]  # tpyc: ok
    print("lc walrus outer", spans, m)


def lc_walrus_source() -> None:
    # an outer filter's str walrus IS the inner clause's source
    chars = [c for i in range(2) if (s := str(i * 7)) for c in s]  # tpyc: ok
    for c in chars:
        print("lc walrus source str", c)
    print("lc walrus source str after", s)
    # ... two clauses further in, under the inner clause's own filter
    deep = [c for w in ["ab", "c"] if (t := w + "!") for k in range(1) for c in t if c != "b"]  # tpyc: ok
    for c in deep:
        print("lc walrus source deep", c)
    # an int walrus bounds the inner range
    print("lc walrus source int", [i * 10 + j for i in range(3) if (n := i + 1) for j in range(n)])  # tpyc: ok
    # a set comprehension over the str walrus
    print("lc walrus source set", len({c for i in range(12) if (u := str(i)) for c in u}), u)  # tpyc: ok


def lc_holder_names(pairs: list[tuple[int, int]]) -> None:
    # user locals spelled like the whole-element holder an unpacking clause
    # binds: the element must read the user's tuples, not the holder
    __comp_tup = (10, 20)
    __comp_tup_1 = (30, 40)
    # one unpacking clause
    print("lc holder single", [__comp_tup[0] + a for a, b in pairs])  # tpyc: ok
    # two unpacking clauses
    print("lc holder two", [__comp_tup[1] + __comp_tup_1[0] + a * p for a, b in pairs for p, q in pairs])  # tpyc: ok
    # a dict comprehension over two unpacking clauses
    print("lc holder dict", {a: __comp_tup_1[1] + q for a, b in pairs for p, q in pairs})  # tpyc: ok


def lc_sources(pairs: list[tuple[C, int]], ds: list[dict[str, int]],
               opts: list[list[C | None]], a: list[list[int]], b: list[int]) -> None:
    # free function: an outer var over pointer-repr tuples reads storage form
    # in the inner range bound and in the element
    print("lc storage tuple", [t[0].n * 10 + k for t in pairs for k in range(t[1])])  # tpyc: ok
    # free function: dict views of the outer var as inner sources
    print("lc items", [k + str(v) for d in ds for k, v in d.items()])  # tpyc: ok
    print("lc values", [v for d in ds for v in d.values()])  # tpyc: ok
    # free function: an Optional-record inner element binds the storage form
    print("lc optional", [c.n if c is not None else -1 for row in opts for c in row])  # tpyc: ok
    # free function: a combinator over the outer var, unpacked
    print("lc zip", [x * y for row in a for x, y in zip(row, b)])  # tpyc: ok


def grow(row: list[int], x: int) -> int:
    if x > 100:
        row.append(x)
    return x


def lc_src_temps() -> None:
    # a walrus with a value-type target over a temporary source copies the
    # value out, so it outlives the source
    out = [c.bump() for c in pick([C(100), C(200)]) for k in range(1) if (last := c.n) > k]  # tpyc: ok
    junk = [C(7), C(8), C(9)]
    print("lc src walrus", out, last, len(junk))
    # ... and over an inner clause's temporary source, rebuilt per outer element
    out_in = [c.n for x in [1, 2] for c in pick([C(x)]) if (both := c.n + x) > 0]  # tpyc: ok
    print("lc src walrus inner", out_in, both)
    # clause 0's source is evaluated in the enclosing scope: the temporary
    # list `pick` lends from outlives the comprehension, so a pointer stored
    # in an element is still valid after the statement
    hs = [CPtr(c) for c in pick([C(100), C(200)]) for k in range(1)]  # tpyc: ok
    junk2 = [C(7), C(8), C(9)]
    h1 = hs[1]
    q = h1.p
    print("lc src ptr", q.n, len(junk2))
    # a comprehension in the element: the inner one's source temporary is
    # rebuilt per outer element
    print("lc src nested", [[c.n + k for c in pick([C(k), C(9)])] for k in range(2)])  # tpyc: ok


def lc_walrus_values() -> None:
    xs = [C(1), C(2)]
    # a walrus with a value-type target over a NAMED source (a walrus binding
    # an object is refused at every source)
    out = [c.bump() for c in xs if (last := c.n) > 0]  # tpyc: ok
    print("lc walrus value", out, last)
    grid = [[C(1)], [C(2)]]
    # ... and over an inner clause whose source is a name
    out2 = [c.bump() for row in grid for c in row if (inner := c.n) > 0]  # tpyc: ok
    print("lc walrus value inner", out2, inner)


def loud(cs: list[C], tag: str) -> list[C]:
    print("lc loud", tag)
    return cs


def lc_range_bounds() -> None:
    # an inner clause's range bounds hold comprehensions over temporary
    # sources: each bound's temps are declared right before its capture,
    # start before stop
    bounded = [j for i in range(2) for j in range(len([c.n for c in loud([C(i)], "start")]), len([c.n for c in loud([C(i), C(i), C(i)], "stop")]))]  # tpyc: ok
    print("lc range bound temps", bounded)
    # the for statement's range bound: the same loop emitter
    for i in range(len([c.n for c in pick([C(1), C(2)])])):  # tpyc: ok
        print("lc for range bound", i)
    # ... a temporary in the start bound
    for i in range(len([c.n for c in pick([C(1), C(2)])]), 4):  # tpyc: ok
        print("lc for range start", i)
    # ... and in the start and the step of a stepped range
    for i in range(len([c.n for c in pick([C(1)])]), 10, len([c.n for c in pick([C(1), C(2), C(3)])])):  # tpyc: ok
        print("lc for range step", i)


def lc_gen_src() -> Iterator[int]:
    # generator body: clause 0's temporary source, values read after a yield
    vals = [c.n + k for c in pick([C(100), C(200)]) for k in range(1)]  # tpyc: ok
    yield 0
    print("lc gen src", vals)
    yield 1


async def lc_async_src() -> int:
    # async def: the same, values read after a suspension
    vals = [c.n + k for c in pick([C(100), C(200)]) for k in range(1)]  # tpyc: ok
    await asyncio.sleep(0)
    print("lc async src", vals)
    return len(vals)


def lc_ranges(n: int, s: int, z: int) -> None:
    # negative literal step into a list (not an Array: the target says list)
    neg: list[int32] = [i for i in range(10, 0, -3)]  # tpyc: ok
    print("lc range neg", neg)
    # negative literal step with a runtime start
    print("lc range neg n", [i for i in range(n, 0, -3)])  # tpyc: ok
    # unit steps with runtime bounds into a list result
    print("lc range unit neg", [i for i in range(n, 0, -1)])  # tpyc: ok
    print("lc range unit pos", [i for i in range(s, n, 1)])  # tpyc: ok
    # a variable step
    print("lc range var", [i for i in range(0, n, s)])  # tpyc: ok
    # 3-arg ranges as an inner clause, both directions
    print("lc range inner", [i * 10 + j for i in range(3) for j in range(i, 7, 3)])  # tpyc: ok
    print("lc range inner neg", [i * 10 + j for i in range(3) for j in range(5, i, -2)])  # tpyc: ok
    # a step too wide for the counter loop iterates the range object instead
    big: list[int64] = [i for i in range(0, 10000000000, 4000000000)]  # tpyc: ok
    print("lc range wide", big)
    # a zero step raises ValueError, a literal one and a runtime one alike
    try:
        zs = [i for i in range(0, 5, 0)]  # tpyc: ok
        print("lc range zero", zs)
    except ValueError:
        print("lc range zero raised")
    try:
        zv = [i for i in range(0, 5, z)]  # tpyc: ok
        print("lc range zero var", zv)
    except ValueError:
        print("lc range zero var raised")


def lc_grow() -> None:
    grid: list[list[int]] = [[1, 2], [3]]
    # an element that may grow the inner source is diagnosed like a for loop
    print("lc grow", [grow(row, x) for row in grid for x in row])  # tpyc: warning(/Passing borrowed container 'row' to non-readonly parameter/)
    # a filter shrinking the inner source is the inner loop's iteration loan
    print("lc shrink", [x for row in grid for x in row if x < 100 or row.pop() > 0])  # tpyc: warning(/Mutation of 'row' while iterating/)


def lc_owned() -> None:
    # free function: the innermost clause over an owned source moves its element
    ws = [w for n in range(3) for w in widgets(n)]  # tpyc: ok
    print("lc owned", [w.id for w in ws])
    # a generator expression whose LAST clause iterates an owned source; the
    # frame copies each element, so a @nocopy one does not build yet
    # (BUGS.md#frame-for-owned-nocopy-element-copies)
    print("lc owned genexpr", sum(c.n for n in range(3) for c in cells(n)))  # tpyc: ok


def lc_copy(grid: list[list[int]]) -> None:
    # a reference element is copied once per inner iteration, and says so
    print("lc copy", [row for row in grid for _ in range(2)])  # tpyc: warning(/copies/)


def lc_bump() -> None:
    grid = [[C(1), C(2)], [C(3)]]
    # free function: mutation through the inner var reaches the records in `grid`
    print("lc bump", [c.bump() for row in grid for c in row])  # tpyc: ok
    print("lc bump seen", [c.n for row in grid for c in row])


class Pairs:
    pairs: list[tuple[int, int]]

    def __init__(self, n: int) -> None:
        # constructor: the member init of a field
        self.pairs = [(i, j) for i in range(n) for j in range(i)]  # tpyc: ok


def lc_gen(rows: list[list[int]]) -> Iterator[int]:
    # generator body
    flat = [x + 1 for row in rows for x in row if x > 1]  # tpyc: ok
    for v in flat:
        yield v


async def lc_async(rows: list[list[int]]) -> int:
    await asyncio.sleep(0)
    # async def: after a suspension
    return len([x for row in rows for x in row if x > 1])  # tpyc: ok


def lc_nested() -> None:
    # annotated for the nested def below
    # (BUGS.md#nested-def-tuple-of-captured-pending-list-ice)
    a_list: list[int] = [1, 2]
    b_list: list[int] = [10, 20]

    def inner() -> Own[list[int]]:
        # nested def: both clauses read the enclosing function's lists
        return [a * b for a in a_list for b in b_list]  # tpyc: ok
    print("lc nested def", inner())


def lc_try(grid: list[list[int]]) -> None:
    try:
        # try/finally body
        print("lc try", [x for row in grid for x in row if x % 2 == 1])  # tpyc: ok
    finally:
        print("lc finally")


class Ctx:
    def __enter__(self) -> "Ctx":
        print("lc enter")
        return self

    def __exit__(self, kind, value, tb) -> None:
        print("lc exit")


def lc_with(grid: list[list[int]]) -> None:
    with Ctx():
        # with body
        print("lc with", {x: len(row) for row in grid for x in row})  # tpyc: ok


class Odd(Exception, ReturnException):
    pass


@error_return(Odd)
def lc_err(grid: list[list[int]]) -> int:
    # @error_return body
    flat = [x * 3 for row in grid for x in row]  # tpyc: ok
    if len(flat) % 2 == 1:
        raise Odd()
    return len(flat)


def lc_match(grid: list[list[int]], k: int) -> None:
    match k:
        case 1:
            # match arm
            print("lc match", [x for row in grid for x in row if x != k])  # tpyc: ok
        case _:
            print("lc match other")


def lc_lambda(grid: list[list[int]]) -> None:
    # lambda body
    total: Callable[[list[list[int]]], int] = lambda rows: sum([x for row in rows for x in row if x > 1])  # tpyc: ok
    print("lc lambda", total(grid))


def lc_main() -> None:
    grid = [[1, 2], [3, 4, 5], [6]]
    lc_src_temps()
    lc_walrus_values()
    lc_range_bounds()
    gen_vals = list(lc_gen_src())
    print("lc gen src", gen_vals)
    async_n = asyncio.run(lc_async_src())
    print("lc async src", async_n)
    lc_ranges(10, 4, 0)
    lc_free(grid, ["ab", "c"], [(1, 2), (3, 4)], [(1, "a"), (2, "b")])
    lc_walrus_source()
    lc_holder_names([(1, 2), (3, 4)])
    lc_sources([(C(1), 2), (C(2), 1)], [{"a": 1}, {"b": 2, "c": 3}],
               [[C(1), None], [C(5)]], [[1, 2], [3]], [10, 20])
    lc_grow()
    lc_owned()
    lc_copy([[1], [2]])
    lc_bump()
    print("lc method", Grid().crossed_list())
    print("lc ctor", Pairs(3).pairs)
    print("lc generator", list(lc_gen([[1, 2], [3]])))
    print("lc async", asyncio.run(lc_async([[1, 2], [3]])))
    lc_nested()
    lc_try(grid)
    lc_with(grid)
    try:
        print("lc error_return", lc_err(grid))
    except Odd:
        print("lc error_return raised")
    lc_match(grid, 1)
    lc_lambda(grid)


def main() -> None:
    free([[1, 2], [3, 4, 5], [6]], ["ab", "c"], [(1, "a"), (2, "b")])
    g = Grid()
    print("method total", g.total())
    print("method crossed", g.crossed())
    grid = [[C(1), C(2)], [C(3)]]
    print("bump sum", bump_all(grid))
    # observed after the boundary: the records in `grid` were bumped in place
    print("bump seen", grid[0][0].n, grid[0][1].n, grid[1][0].n)
    bump_nested()
    print("generator", list(gen([[1, 2], [3]])))
    print("async", asyncio.run(work([[1, 2], [3]])))
    captured()
    captured_places(1000)
    kept([1, 2])
    genexpr_positions([[1, 2], [3]])
    lc_main()


main()
# module level: a two-clause genexpr consumed in place
print("module", sum(x * y for x in range(1, 3) for y in range(x, 4)))  # tpyc: ok
# module level: comp vars of both clauses shadow globals of the same name
lc_row = [9]
lc_x = -1
lc_grid = [[1, 2], [3]]
print("lc module shadow", [lc_x for lc_row in lc_grid for lc_x in lc_row], lc_row, lc_x)  # tpyc: ok
# module level: a two-clause list comprehension bound to a global
lc_pairs = [(i, j) for i in range(3) for j in range(i)]  # tpyc: ok
print("lc module", lc_pairs, [x * y for x in range(1, 3) for y in range(x, 4)])  # tpyc: ok
# module level: clause 0 over a temporary source, inner clause a range
print("lc module src", [c.n for c in pick([C(5)]) for j in range(2)])  # tpyc: ok
