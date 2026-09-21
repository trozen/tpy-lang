# A generator expression is a generator frame: its source is the frame's first
# slot (a borrowed container by reference, an rvalue built in place), every
# enclosing name the element and filters read is a REFERENCE capture, and the
# body runs at each pull. One section per position, consumer, capture and
# source the other genexpr cases leave out; iterators/genexpr_rvalue_sources
# pins the source kinds and builtins/combinator_owned_source_moves the pinned
# source. Reference elements are mutated through the loop var and read back, so
# a copy where CPython aliases would change the output.
from __future__ import annotations
import asyncio
from typing import Iterable, Iterator
from tpy import int32, Own, Fn, Comparable, readonly
from holder import Holder, mapped


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def bump(self) -> int32:
        self.v += 10
        return self.v


class Noisy:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> Own[Cur]:
        print("  Noisy.__iter__", self.n)
        return Cur(self.n)


class Cur:
    i: int32
    n: int32

    def __init__(self, n: int32) -> None:
        self.i = 0
        self.n = n

    def __iter__(self) -> Cur:
        return self

    def __next__(self) -> int32:
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i


class Acc:
    items: list[int32]
    k: int32
    t: int32

    def __init__(self, xs: list[int32]) -> None:
        self.items = [1, 2, 3]
        self.k = 5
        # constructor body: a param source, stored into a field.
        self.t = sum(x * 2 for x in xs)  # tpyc: ok

    def tot(self) -> int32:
        # method: `self` is a capture, a field is the borrowed source.
        return sum(x * self.k for x in self.items)  # tpyc: ok

    def make(self) -> Own[list[int32]]:
        return [4, 5, 6]


class Bag[T]:
    items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self.items = items

    def count(self) -> int32:
        # a method of a GENERIC class: the frame takes the class's type params.
        return sum(1 for v in self.items)  # tpyc: ok


class Ledger:
    rows: list[int32]
    rate: int32
    lo: int32
    hi: int32
    total: int32

    def __init__(self, rows: list[int32], rate: int32) -> None:
        # a constructor too large for the header: its frame goes to the .cpp
        # with it, while `Acc`'s small bodies keep theirs in the header.
        self.rows = [r for r in rows]
        self.rate = rate
        self.lo = 0
        self.hi = 0
        self.total = 0
        for r in rows:
            if r < self.lo or self.lo == 0:
                self.lo = r
            if r > self.hi:
                self.hi = r
        self.total = sum(r * rate for r in rows)  # tpyc: ok

    def audit(self, floor: int32) -> int32:
        # a method too large for the header, same placement.
        kept = 0
        dropped = 0
        for r in self.rows:
            if r >= floor:
                kept += 1
            else:
                dropped += 1
        print("  audit kept", kept, "dropped", dropped)
        return sum(r * self.rate for r in self.rows if r >= floor)  # tpyc: ok


class Shelf:
    class Box:
        items: list[int32]
        scale: int32

        def __init__(self) -> None:
            self.items = [1, 2, 3]
            self.scale = 2

        def weigh(self, floor: int32) -> int32:
            # a nested record's method, too large for the header: its frame
            # sits with the outermost record's definitions in the .cpp.
            light = 0
            heavy = 0
            for i in self.items:
                if i >= floor:
                    heavy += 1
                else:
                    light += 1
            print("  weigh heavy", heavy, "light", light)
            return sum(i * self.scale for i in self.items if i >= floor)  # tpyc: ok


def gen(n: int32) -> Iterator[int32]:
    for i in range(n):
        print("  gen", i)
        yield i


def total(it: Iterable[int32]) -> int32:
    s = 0
    for v in it:
        s += v
    return s


def relay(it: Iterable[int32]) -> Iterator[int32]:
    for v in it:
        yield v + 100


def dbl(v: int32) -> int32:
    return v * 2


def score(n: Node) -> int32:
    return n.v * 2


def chk(v: int32) -> int32:
    if v == 2:
        raise ValueError("two")
    return v


def note(acc: list[int32], v: int32) -> int32:
    acc.append(v)
    return len(acc)


def bump_all(nodes: list[Node]) -> int32:
    # the body mutates through its loop var: the enclosing PARAM it iterates
    # has to stay a mutable borrow.
    return sum(n.bump() for n in nodes)  # tpyc: ok


def note_all(nodes: list[Node], acc: list[int32]) -> int32:
    # a captured param mutated through a callee.
    return sum(note(acc, n.v) for n in nodes)  # tpyc: ok


def bump_yielded(nodes: list[Node]) -> None:
    # the CONSUMER mutates through the element the genexpr lends.
    for n in (n for n in nodes):  # tpyc: ok
        n.v += 100


def apply(f: Fn[[int32], int32], v: int32) -> int32:
    return f(v)


def first_big[T: Comparable](xs: list[T], lim: T) -> bool:
    # generic function: the frame takes the enclosing type params.
    return any(lim < x for x in xs)  # tpyc: ok


def narrowed(xs: list[int32], k: int32 | None) -> int32:
    if k is None:
        return 0
    # a consumer that pulls to the end inside one expression: nothing can
    # rebind `k` between two pulls, so its narrowing holds in the body.
    return sum(x * k for x in xs)  # tpyc: ok


def narrowed_for_head(xs: list[int32], k: int32 | None) -> None:
    if k is None:
        return
    # a `for` head whose body leaves `k` alone: the narrowing holds, no warning.
    for v in (x * k for x in xs):  # tpyc: ok
        print("narrowed_for_head", v)


def narrowed_closure_written(xs: list[int32], k: int32 | None) -> int32:
    if k is None:
        return 0

    def reset() -> None:
        nonlocal k
        k = 7

    # a closure writes the captured name, so the body checks its reads.
    t = sum(x * k for x in xs)  # tpyc: warning(/Potential None access/)
    reset()
    return t + sum(x * k for x in xs)  # tpyc: warning(/Potential None access/)


def narrowed_loop_closure(xs: list[int32], k: int32 | None) -> int32:
    if k is None:
        return 0
    t = 0
    # the loop body DEFINES a closure that writes the captured name.
    for v in (x * k for x in xs):  # tpyc: warning(/Potential None access/)
        def bump() -> None:
            nonlocal k
            k = 9
        t += v
        bump()
    return t


def narrowed_kept(xs: list[int32], k: int32 | None) -> None:
    if k is None:
        return
    # a lazy consumer bound to a name keeps the genexpr alive, but `k` is
    # rebound only after the last read of `g`: the narrowing holds.
    g = relay(x * k for x in xs)  # tpyc: ok
    for v in g:
        print("narrowed_kept", v)
    k = None
    print("narrowed_kept", k)


def count_set(opts: list[Node | None]) -> int32:
    # a read-only pass over Optional elements: the source param stays const,
    # and so does the nullable pointer the loop var is.
    return sum(q.v for q in opts if q is not None)  # tpyc: ok


def count_readonly(opts: readonly[list[Node | None]]) -> int32:
    # the same pass over a READONLY container.
    return sum(q.v for q in opts if q is not None)  # tpyc: ok


def pairs_of(ns: list[Node]) -> Iterator[tuple[int32, Node]]:
    i = 0
    for n in ns:
        yield (i, n)
        i += 1


def narrowed_rebound(xs: list[int32], k: int32 | None) -> None:
    if k is None:
        return
    # the loop body rebinds the captured name between two pulls, so the body
    # reads `k` at its declared type and checks it.
    for v in (x * k for x in xs):  # tpyc: warning(/Potential None access/)
        print("narrowed_rebound", v)
        k = v


def scores(n: int32) -> Iterator[int32]:
    # a plain `def` generator: a record temporary handed to a callee in a VALUE
    # yield flushes ahead of the `return` (the frame arm a genexpr element uses).
    for i in range(n):
        yield score(Node(i))  # tpyc: ok


def bound(n: int32) -> int32:
    print("  bound", n)
    return n


def walk(n: int32) -> Iterator[int32]:
    # generator body: the source and the capture are locals of THIS frame.
    xs = [i for i in range(n)]
    k = 2
    yield sum(y * k for y in xs)  # tpyc: ok
    k += 1
    yield sum(y * k for y in xs)


async def aio(xs: list[int32]) -> int32:
    k = 3
    await asyncio.sleep(0)
    # async body: a coroutine-frame local captured after a suspension.
    return sum(x * k for x in xs)  # tpyc: ok


def by_match(n: int32, xs: list[int32]) -> int32:
    try:
        match n:
            case 1:
                return sum(x for x in xs if x > n)  # tpyc: ok
            case _:
                return sum(x * n for x in xs)
    finally:
        print("by_match finally")


# module level: a combinator source over globals.
gxs = [1, 2, 3]
gys = [10, 20, 30]
module_total = sum(a * b for a, b in zip(gxs, gys))  # tpyc: ok
print("module_level", module_total)
# a literal of literals at module level: no enclosing function settles it.
ggrid = [[1], [2, 3]]
print("module_nested_literal", sum(len(r) for r in ggrid))  # tpyc: ok


def main() -> None:
    xs = [1, 2, 3]
    ys = [10, 20, 30]
    flag = len(xs) > 2

    # positions with no statement-level slot for a temporary.
    print("and_operand", flag and sum(a * b for a, b in zip(xs, ys)) > 3)  # tpyc: ok
    pinned = flag and sum(a * b for a, b in zip(Noisy(2), ys)) > 3
    print("and_pinned", pinned)
    print("ternary", sum(a * b for a, b in zip(xs, ys)) if flag else 0)
    if xs and any(x > 2 for x in xs):
        print("if_condition big")
    n = 0
    while any(x > n for x in xs):
        n += 1
    print("while_head", n)
    rows = [[1, 2], [3, 4]]
    print("comp_element", [sum(x * 2 for x in r) for r in rows])

    # closures: the genexpr captures what the closure captured.
    k = 2
    print("lambda", apply(lambda q: sum(x * k + q for x in xs), 1))

    def inner(q: int32) -> int32:
        return sum(x * k + q for x in xs)

    print("nested_def", inner(1))

    def own_grid() -> int32:
        # a literal of literals created INSIDE the nested def settles with it.
        local_rows = [[1], [2, 3]]
        return sum(len(r) for r in local_rows)  # tpyc: ok

    print("nested_def_own_literal", own_grid())

    # consumers.
    for v in (x * 2 for x in xs if x != 2):  # tpyc: ok
        print("for_head", v)
    print("iterable_param", total(x * 2 for x in xs))
    print("generator_param", list(relay(x * 2 for x in xs)))
    for i, v in enumerate(x * 2 for x in xs):
        print("owning_enumerate", i, v)
    for a, b in zip((x * 2 for x in xs), (y + 1 for y in ys)):
        print("zip_two", a, b)
    print("map_over", list(map(dbl, (x + 1 for x in xs))))
    print("str_join", ",".join(str(x) for x in xs))
    short = any(v > 1 for v in gen(5))
    print("any_short_circuit", short)

    # captures are references: a rebind between two pulls is visible.
    m = 10
    for v in (x * m for x in xs):  # tpyc: ok
        m += 1
        print("rebind_observed", v)
    seen: list[int32] = []
    for v in (x + len(seen) for x in xs):
        seen.append(v)
    print("ref_mutated_live", seen)
    t = 0
    for i in range(3):
        t += sum(x * i for x in xs)
    print("enclosing_loop_var", t)
    print("walrus", sum(y for x in xs if (y := x * 2) > 2))
    print("generic", first_big(xs, 2), first_big([1.5, 2.5], 3.0))
    print("narrowed", narrowed(xs, 2), narrowed(xs, None))
    narrowed_rebound(xs, 2)

    # sources.
    acc = Acc(xs)
    print("ctor_init", acc.t, "method", acc.tot())
    # a kept lazy consumer sees a capture rebound between two pulls.
    m2 = 10
    kept_gen = relay(x * m2 for x in xs)  # tpyc: ok
    for v in kept_gen:
        m2 += 1
        print("kept_rebindable", v)
    # another module's small method and its `Fn`-param generator: both frames
    # are templates this module instantiates from that module's header.
    holder = Holder(2)
    print("cross_module_method", holder.small(xs))
    print("cross_module_fn_generator", list(mapped(dbl, xs)))
    boxed = Shelf.Box()
    weighed = boxed.weigh(2)
    print("nested_record_method", weighed)
    led = Ledger(xs, 3)
    audited = led.audit(2)
    print("source_ctor", led.total, led.lo, led.hi, "source_method", audited)
    print("method_own_list", sum(x for x in acc.make()))
    print("field", sum(x for x in acc.items))
    s = {1, 2, 3}
    d = {1: 10, 2: 20}
    print("set", sum(x for x in s), "dict", sum(a * b for a, b in d.items()))
    print("range", sum(i * i for i in range(4)), sum(i for i in range(10, 0, -3)))

    # a literal of literals: the loop var's own container type settles only
    # when this function ends, after the frame that holds it was built. The
    # second one is mutated through the loop var, so a copy would show.
    grid = [[1], [2, 3]]
    print("nested_literal", sum(len(r) for r in grid), sum(sum(y for y in r) for r in grid))  # tpyc: ok
    bins = [[1], [2, 3]]
    for b in (b for b in bins if len(b) > 1):  # tpyc: ok
        b.append(9)
    print("nested_literal_mutated", bins)

    # elements.
    print("multi_filter", sum(x for x in [1, 2, 3, 4, 5, 6] if x > 1 if x % 2 == 0))
    print("tuple_elem", list((x, x * x) for x in xs))
    try:
        print(sum(chk(x) for x in xs))
    except ValueError as e:
        print("raises", e)
    # a record temporary handed to a callee in the element and in the filter.
    print("record_temp", sum(score(Node(i)) for i in range(4) if score(Node(i)) > 0))  # tpyc: ok
    # a reference element is lent through the yield: the mutation reaches the list.
    ns = [Node(1), Node(2)]
    for nd in (q for q in ns if q.v > 0):  # tpyc: ok
        nd.v += 10
    print("ref_elem", ns[0].v, ns[1].v)
    # a record unpacked from a tuple aliases the source element.
    pairs = [(Node(1), 2), (Node(3), 4)]
    print("record_unpack", sum(p.bump() for p, q in pairs), [p.v for p, q in pairs])
    print("record_unpack_literal", sum(q for p, q in [(Node(1), 10)]))

    # mutation facts reach the enclosing function's params.
    ms = [Node(1), Node(2)]
    print("param_loop_var_mutated", bump_all(ms), ms[0].v, ms[1].v)
    noted: list[int32] = []
    print("param_capture_mutated", note_all(ms, noted), noted)
    bump_yielded(ms)
    print("param_yield_mutated", ms[0].v, ms[1].v)

    # a record unpacked out of a combinator's PROXY tuple aliases the source.
    zs = [Node(1), Node(2)]
    print("zip_unpack", sum(p.bump() for p, q in zip(zs, ys)), zs[0].v, zs[1].v)
    print("enumerate_unpack", sum(z.bump() for i, z in enumerate(zs)), zs[0].v, zs[1].v)
    dn = {1: Node(3), 2: Node(4)}
    print("items_unpack", sum(z.bump() for key, z in dn.items()), dn[1].v, dn[2].v)
    names = {"ab": 1, "cde": 2}
    print("view_unpack", sum(len(key) + val for key, val in names.items()))
    # "filter out the Nones": the narrowed element is lent, not copied.
    opts: list[Node | None] = [Node(1), None, Node(3)]
    for o in (q for q in opts if q is not None):  # tpyc: ok
        o.v += 10
    first = opts[0]
    print("optional_filter", first.v if first is not None else -1)
    print("optional_readonly", count_set(opts), count_readonly(opts))
    # Optional elements off a dict view: a deduced source, the same nullable pointer.
    od: dict[int32, Node | None] = {1: Node(5), 2: None}
    for q in (q for q in od.values() if q is not None):  # tpyc: ok
        q.v += 100
    print("optional_view", sum(q.v for q in od.values() if q is not None))
    # a record unpacked from the tuples a GENERATOR lends.
    gs = [Node(1), Node(2)]
    print("generator_unpack", sum(i + n.bump() for i, n in pairs_of(gs)), gs[0].v, gs[1].v)
    # names the frame also uses for its own params stay the enclosing ones.
    make = 3
    __r0 = 3
    __src = 2
    print("capture_names", sum(x * make for x in [1, 2, 3]),
          sum(x * __r0 for x in range(4)), sum(x * __src for x in [1, 2]),
          sum(y for __src, y in zip(xs, ys)))
    # a captured record local the enclosing body re-seats between two genexprs.
    cur = Node(1)
    kept = cur
    before = sum(x + cur.v for x in xs)
    cur = Node(100)
    print("capture_reseated", before, sum(x + cur.v for x in xs), kept.v)
    # range bounds are taken where the genexpr is written, once.
    top = 3
    for v in (i for i in range(bound(top))):  # tpyc: ok
        top += 1
        print("range_bound", v)
    print("generic_method", Bag([1, 2, 3]).count(), Bag(["a"]).count())
    narrowed_for_head(xs, 2)
    print("narrowed_loop_closure", narrowed_loop_closure(xs, 2))
    narrowed_kept(xs, 2)
    print("narrowed_closure_written", narrowed_closure_written(xs, 2))
    print("def_yield_temp", list(scores(3)))

    print("generator_body", list(walk(4)))
    print("async", asyncio.run(aio(xs)))
    ra = by_match(1, xs)
    rb = by_match(2, xs)
    print("match", ra, rb)


main()
