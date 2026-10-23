# A loop element written through a callee, a receiver or an alias reaches the
# loop's source (printed after each section); the inverse sections stay const.
import asyncio
from typing import Iterator

from tpy import Array, Own, auto_readonly, dispatch, int32


class P:
    def __init__(self, n: int32) -> None:
        self.n = n

    def inc(self) -> None:
        self.n += 1

    def inc_get(self) -> int32:
        self.n += 1
        return self.n

    def peek(self) -> int32:
        return self.n


def ns(ps: list[P]) -> Own[list[int32]]:
    return [p.n for p in ps]


# writer defined ABOVE its callers
def bump(p: P) -> None:
    p.n += 1


def bumped(p: P) -> int32:
    p.n += 1
    return p.n


# two calls deep, both defined above
def via_above(p: P) -> None:
    bump(p)


class Poker:
    def poke(self, p: P) -> None:
        p.n += 10


@dispatch
def bump_any(p: P) -> None:
    p.n += 100


@dispatch
def bump_any(p: int32) -> None:
    print("int", p)


class R:
    def __init__(self, s: str) -> None:
        self.s = s


class Q:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = []

    @auto_readonly
    def items(self) -> list[int32]:
        return self.xs


# --- positions ---

# free function
def free_fn(ps: list[P]) -> None:
    for p in ps:
        bump(p)  # tpyc: ok


class W:
    # method
    def run(self, ps: list[P]) -> None:
        for p in ps:
            bump(p)  # tpyc: ok

    # @staticmethod
    @staticmethod
    def srun(ps: list[P]) -> None:
        for p in ps:
            bump(p)  # tpyc: ok


class Init:
    k: int32

    # constructor
    def __init__(self, ps: list[P]) -> None:
        for p in ps:
            bump(p)  # tpyc: ok
        self.k = len(ps)


# async, with an await inside the loop
async def arun(ps: list[P]) -> None:
    for p in ps:
        bump(p)  # tpyc: ok
        await asyncio.sleep(0)


# generator
def gen(ps: list[P]) -> Iterator[int32]:
    for p in ps:
        bump(p)  # tpyc: ok
        yield p.n


# generator expression
def genexpr(ps: list[P]) -> int32:
    return sum(bumped(p) for p in ps)  # tpyc: ok


# nested def writing the element of its own loop over a capture
def nested(ps: list[P]) -> None:
    def inner(q: P) -> None:
        bump(q)
    for p in ps:
        inner(p)  # tpyc: ok


# a loop inside a nested def over the enclosing function's parameter
def closure_loop(ps: list[P]) -> None:
    def inner() -> None:
        for p in ps:
            bump(p)  # tpyc: ok
    inner()


class Cm:
    def __enter__(self) -> "Cm":
        return self

    def __exit__(self, et, ev, tb) -> None:
        print("with: exit")


# with body
def with_body(ps: list[P]) -> None:
    with Cm():
        for p in ps:
            bump(p)  # tpyc: ok


# try / finally
def try_finally(ps: list[P]) -> None:
    try:
        for p in ps:
            bump(p)  # tpyc: ok
    finally:
        print("try: finally")


# match arm
def match_arm(k: int32, ps: list[P]) -> None:
    match k:
        case 1:
            for p in ps:
                bump(p)  # tpyc: ok
        case _:
            pass


# a write through an alias of the loop variable: match capture, walrus, ternary
def alias_writes(ps: list[P], k: bool) -> None:
    for x in ps:
        match x:
            case c:
                c.n += 10  # tpyc: ok
        if (w := x).n > 0:
            w.n += 100  # tpyc: ok
        y = x if k else x
        y.n += 1000  # tpyc: ok


# a match capture of a union element
def union_capture(us: list[P | R]) -> None:
    for u in us:
        match u:
            case P() as c:
                c.n += 10  # tpyc: ok
            case _:
                pass


def uns(us: list[P | R]) -> Own[list[int32]]:
    out: list[int32] = []
    for u in us:
        if isinstance(u, P):
            out.append(u.n)
    return out


# a tuple element alias of the loop variable
def tuple_alias(ps: list[P]) -> None:
    for x in ps:
        t = (x, 1)
        t[0].n += 10  # tpyc: ok


# list / dict / set comprehensions and a two-clause one
def list_comp(ps: list[P]) -> Own[list[int32]]:
    return [bumped(p) for p in ps]  # tpyc: ok


def dict_comp(ps: list[P]) -> Own[dict[int32, int32]]:
    return {i: bumped(p) for i, p in enumerate(ps)}  # tpyc: ok


def set_comp(ps: list[P]) -> Own[set[int32]]:
    return {bumped(p) for p in ps}  # tpyc: ok


def two_clause(rows: list[list[P]]) -> Own[list[int32]]:
    return [bumped(p) for row in rows for p in row]  # tpyc: ok


# zip in a comprehension: the write reaches its own slot's source only
def comp_zip(ps: list[P], qs: list[P]) -> Own[list[int32]]:
    return [bumped(p) for p, q in zip(ps, qs)]  # tpyc: ok


# --- sources ---

def varargs(*ps: P) -> None:
    for p in ps:
        bump(p)  # tpyc: ok


def array(ps: Array[P, 2]) -> None:
    for p in ps:
        bump(p)  # tpyc: ok


def dict_values(d: dict[str, P]) -> None:
    for p in d.values():
        bump(p)  # tpyc: ok


def dict_items(d: dict[str, P]) -> None:
    for k, p in d.items():
        bump(p)  # tpyc: ok


def tuple_target(pairs: list[tuple[P, P]]) -> None:
    for a, b in pairs:
        bump(a)  # tpyc: ok


# zip: the write reaches its own slot's source only
def zip_slot(ps: list[P], qs: list[P]) -> None:
    for p, q in zip(ps, qs):
        bump(p)  # tpyc: ok


def enumerated(ps: list[P]) -> None:
    for i, p in enumerate(ps):
        bump(p)  # tpyc: ok


def reversed_src(ps: list[P]) -> None:
    for p in reversed(ps):
        bump(p)  # tpyc: ok


class Bag:
    items: list[P]

    def __init__(self) -> None:
        self.items = [P(1), P(2)]

    # a self field source
    def run(self) -> None:
        for p in self.items:
            bump(p)  # tpyc: ok

    # a mutating receiver over a self field
    def run_receiver(self) -> None:
        for p in self.items:
            p.inc()  # tpyc: ok

    # a nested def writing the element of a loop over a self field
    def run_closure(self) -> None:
        for p in self.items:
            def f() -> None:
                bump(p)  # tpyc: ok
            f()

    # a loop over a self field inside a nested def
    def closure_loop(self) -> None:
        def g() -> None:
            for p in self.items:
                bump(p)  # tpyc: ok
        g()


# nested loops: the outer binding is written too
def nested_loops(rows: list[list[P]]) -> None:
    for row in rows:
        for p in row:
            bump(p)  # tpyc: ok


# a nested def writing an enclosing loop's variable: both loops bind mutable
def nested_def_write(rows: list[list[P]]) -> None:
    for row in rows:
        for p in row:
            def g() -> None:
                p.n += 1  # tpyc: ok
            g()


# a generator expression over an enclosing loop variable
def genexpr_in_loop(rows: list[list[P]]) -> int32:
    t = 0
    for row in rows:
        t += sum(bumped(p) for p in row)  # tpyc: ok
    return t


# an enumerate head inside an outer loop
def enumerate_in_loop(rows: list[list[P]]) -> None:
    for row in rows:
        for i, p in enumerate(row):
            bump(p)  # tpyc: ok


# --- writers ---

def other_method(w: Poker, ps: list[P]) -> None:
    for p in ps:
        w.poke(p)  # tpyc: ok


def deep_above(ps: list[P]) -> None:
    for p in ps:
        via_above(p)  # tpyc: ok


def deep_below(ps: list[P]) -> None:
    for p in ps:
        via_below(p)  # tpyc: ok


def dispatched(ps: list[P]) -> None:
    for p in ps:
        bump_any(p)  # tpyc: ok


def receiver(ps: list[P]) -> None:
    for p in ps:
        p.inc()  # tpyc: ok



# a mutating receiver through an alias of the loop variable
def alias_receiver(ps: list[P]) -> None:
    for p in ps:
        q = p
        q.inc()  # tpyc: ok


# a mutating receiver inside a nested def over the loop variable
def nested_receiver(ps: list[P]) -> None:
    for p in ps:
        def f() -> None:
            p.inc()  # tpyc: ok
        f()


# a nested def looping over a captured alias of the parameter: a receiver
# writer and a free-function writer
def closure_alias_receiver(ps: list[P]) -> None:
    ys = ps
    def inner() -> None:
        for p in ys:
            p.inc()  # tpyc: ok
    inner()


def closure_alias_arg(ps: list[P]) -> None:
    ys = ps
    def inner() -> None:
        for p in ys:
            bump(p)  # tpyc: ok
    inner()


# a nested def writing an inner loop's variable whose source aliases the
# outer loop's binding
def outer_alias_nested(rows: list[list[P]]) -> None:
    for row in rows:
        r = row
        for p in r:
            def f() -> None:
                p.inc()  # tpyc: ok
            f()

# a local source, the writer two calls deep above the loop
def local_source() -> Own[list[int32]]:
    ps = [P(1), P(2)]
    for p in ps:
        via_above(p)  # tpyc: ok
    return ns(ps)


# a comprehension target named like a parameter writes its own element, not
# the parameter
def comp_shadow(n: P, ms: list[P]) -> Own[list[int32]]:
    return [n.inc_get() for n in ms]  # tpyc: ok


# an alias of a parameter written inside a comprehension whose target shadows
# the alias's source: the write reaches the parameter, not the target
def comp_shadow_alias(a: P, qs: list[P]) -> None:
    b = a
    r = [b.inc_get() for a in qs]  # tpyc: ok
    # the same write as an argument
    r2 = [bumped(b) for a in qs]  # tpyc: ok
    print("comp_shadow_alias:", r, r2, a.n, ns(qs))


# the loop twin: the alias's source is the outer loop's binding
def loop_shadow_alias(ps: list[P], qs: list[P]) -> None:
    for a in ps:
        b = a
        r = [b.inc_get() for a in qs]  # tpyc: ok
        r2 = [bumped(b) for a in qs]  # tpyc: ok
        print("loop_shadow_alias:", r, r2, a.n)


class H:
    bp: tuple[P, P]

    def __init__(self, n: int32) -> None:
        self.bp = (P(n), P(n))


# a borrow taken in one loop, written after a later loop reused the loop
# variable's name: the write reaches the first loop's source only
def reused_loop_name(ros: list[H], others: list[H], m: H) -> int32:
    s = 0
    t = m.bp
    for r in ros:
        t = r.bp
    for r in others:
        s += r.bp[1].n
    t[0].n += 1000  # tpyc: ok
    return s


# a borrow taken from the loop source on either branch, under one loop
# variable name: the write after the join reaches both branches' sources
def join_loop_name(c: bool, ros: list[H], others: list[H], m: H) -> None:
    t = m.bp
    if c:
        for r in ros:
            t = r.bp
    else:
        for r in others:
            t = r.bp
    t[0].n += 1000  # tpyc: ok


def raise_if(k: int32) -> None:
    if k > 0:
        raise ValueError("k")


# the alias's source rebound in try / except before the write: the write
# reaches whichever source the alias holds
def try_rebound(k: int32, ros: list[H], others: list[H], m: H) -> None:
    t = m.bp
    try:
        for r in ros:
            t = r.bp
        raise_if(k)
    except ValueError:
        for r in others:
            t = r.bp
    t[0].n += 1000  # tpyc: ok


# --- inverse sections ---

# read-only loop: the binding and the source stay const
def read_only(ps: list[P]) -> int32:
    t = 0
    for p in ps:
        t += p.n  # tpyc: ok
    return t


# a receiver method inferred const keeps the source const
def const_receiver(ps: list[P]) -> int32:
    t = 0
    for p in ps:
        t += p.peek()  # tpyc: ok
    return t


# a const receiver method through an alias keeps the source const
def const_alias_receiver(ps: list[P]) -> int32:
    t = 0
    for p in ps:
        q = p
        t += q.peek()  # tpyc: ok
    return t


# a const receiver method inside a nested def keeps the source const
def const_nested_receiver(ps: list[P]) -> int32:
    t = 0
    for p in ps:
        def f() -> int32:
            return p.peek()
        t += f()  # tpyc: ok
    return t


# an @auto_readonly receiver written through its returned alias keeps the
# mutable clone, so the source binds mutable
def auto_readonly_alias(qs: list[Q]) -> None:
    for q in qs:
        ys = q.items()
        ys.append(1)  # tpyc: ok


# a capture rebound before the nested def: the loop writes the new list's
# elements and the parameter's stay unchanged (it still binds mutable, as in
# the direct twin: BUGS.md#rebound-alias-keeps-source-mutable)
def closure_rebound_capture(ps: list[P]) -> None:
    ys = ps
    ys = [P(9)]
    def inner() -> None:
        for p in ys:
            bump(p)  # tpyc: ok
    inner()
    print("closure_rebound_capture: ys", ns(ys))


# --- rebinding += (a type with only __add__) vs in-place += (__iadd__) ---

class Acc:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[Acc]":
        return Acc(self.n + k)


class IAcc:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __iadd__(self, k: int32) -> "IAcc":
        self.n += k
        return self


# an owned local nothing aliases: += falls back to __add__ and rebinds it
def aug_owned_local() -> int32:
    a = Acc(1)
    a += 10  # tpyc: ok
    return a.n


# through nonlocal, an owned enclosing local: += falls back to __add__ and
# rebinds it
def aug_nonlocal_owned() -> int32:
    w = Acc(1)
    def g() -> None:
        nonlocal w
        w += 10  # tpyc: ok
    g()
    return w.n


# through nonlocal, a value type
def aug_nonlocal_value() -> int32:
    n = 1
    def g() -> None:
        nonlocal n
        n += 10  # tpyc: ok
    g()
    return n


# through nonlocal, __iadd__ on an enclosing local bound to an element updates
# the element in place
def aug_nonlocal_iadd(xs: list[IAcc]) -> None:
    w = xs[0]
    def g() -> None:
        nonlocal w
        w += 10  # tpyc: ok
    g()
    print("aug_nonlocal_iadd: w", w.n)


# __iadd__ on a zip head updates the element in place: the source sees it
def aug_iadd_zip(xs: list[IAcc], ks: list[int32]) -> None:
    for x, k in zip(xs, ks):
        x += k  # tpyc: ok


# the spelled-out rebind on a zip head: the source keeps its elements
def rebind_zip(vs: list[Acc], ks: list[int32]) -> None:
    for v, k in zip(vs, ks):
        v = v + k  # tpyc: ok
        print("rebind_zip: v", v.n)


# writers defined BELOW their callers
def via_below(p: P) -> None:
    bump_below(p)


def bump_below(p: P) -> None:
    p.n += 1


def main() -> None:
    ps = [P(1), P(2)]
    free_fn(ps)
    print("free_fn:", ns(ps))
    ps = [P(1), P(2)]
    W().run(ps)
    print("method:", ns(ps))
    ps = [P(1), P(2)]
    W.srun(ps)
    print("staticmethod:", ns(ps))
    ps = [P(1), P(2)]
    o = Init(ps)
    print("ctor:", ns(ps), o.k)
    ps = [P(1), P(2)]
    asyncio.run(arun(ps))
    print("async:", ns(ps))
    ps = [P(1), P(2)]
    for v in gen(ps):
        print("generator: yield", v)
    print("generator:", ns(ps))
    ps = [P(1), P(2)]
    print("genexpr:", genexpr(ps), ns(ps))
    ps = [P(1), P(2)]
    nested(ps)
    print("nested_def:", ns(ps))
    ps = [P(1), P(2)]
    closure_loop(ps)
    print("closure_loop:", ns(ps))
    ps = [P(1), P(2)]
    with_body(ps)
    print("with:", ns(ps))
    ps = [P(1), P(2)]
    try_finally(ps)
    print("try:", ns(ps))
    ps = [P(1), P(2)]
    match_arm(1, ps)
    print("match:", ns(ps))
    ps = [P(1), P(2)]
    alias_writes(ps, True)
    print("alias_writes:", ns(ps))
    us: list[P | R] = [P(1), R("r"), P(2)]
    union_capture(us)
    print("union_capture:", uns(us))
    ps = [P(1), P(2)]
    tuple_alias(ps)
    print("tuple_alias:", ns(ps))
    ps = [P(1), P(2)]
    print("list_comp:", list_comp(ps), ns(ps))
    ps = [P(1), P(2)]
    print("dict_comp:", dict_comp(ps), ns(ps))
    ps = [P(1), P(2)]
    print("set_comp:", sorted(set_comp(ps)), ns(ps))
    rows = [[P(1)], [P(2), P(3)]]
    print("two_clause:", two_clause(rows), ns(rows[0]), ns(rows[1]))
    ps = [P(1), P(2)]
    qs = [P(5), P(6)]
    print("comp_zip:", comp_zip(ps, qs), ns(ps), ns(qs))

    a = P(1)
    b = P(2)
    varargs(a, b)
    print("varargs:", a.n, b.n)
    arr: Array[P, 2] = [P(1), P(2)]
    array(arr)
    print("array:", arr[0].n, arr[1].n)
    d = {"a": P(1), "b": P(2)}
    dict_values(d)
    print("dict_values:", d["a"].n, d["b"].n)
    dict_items(d)
    print("dict_items:", d["a"].n, d["b"].n)
    pairs = [(P(1), P(5)), (P(2), P(6))]
    tuple_target(pairs)
    print("tuple_target:", pairs[0][0].n, pairs[0][1].n, pairs[1][0].n)
    ps = [P(1), P(2)]
    qs = [P(5), P(6)]
    zip_slot(ps, qs)
    print("zip:", ns(ps), ns(qs))
    ps = [P(1), P(2)]
    enumerated(ps)
    print("enumerate:", ns(ps))
    ps = [P(1), P(2)]
    reversed_src(ps)
    print("reversed:", ns(ps))
    bag = Bag()
    bag.run()
    print("self_items:", ns(bag.items))
    bag.run_closure()
    print("self_items_closure:", ns(bag.items))
    bag.closure_loop()
    print("self_items_closure_loop:", ns(bag.items))
    bag.run_receiver()
    print("self_items_receiver:", ns(bag.items))
    rows = [[P(1)], [P(2), P(3)]]
    nested_loops(rows)
    print("nested_loops:", ns(rows[0]), ns(rows[1]))
    rows = [[P(1)], [P(2), P(3)]]
    nested_def_write(rows)
    print("nested_def_write:", ns(rows[0]), ns(rows[1]))
    rows = [[P(1)], [P(2), P(3)]]
    print("genexpr_in_loop:", genexpr_in_loop(rows), ns(rows[0]), ns(rows[1]))
    rows = [[P(1)], [P(2), P(3)]]
    enumerate_in_loop(rows)
    print("enumerate_in_loop:", ns(rows[0]), ns(rows[1]))

    ps = [P(1), P(2)]
    other_method(Poker(), ps)
    print("other_method:", ns(ps))
    ps = [P(1), P(2)]
    deep_above(ps)
    print("deep_above:", ns(ps))
    ps = [P(1), P(2)]
    deep_below(ps)
    print("deep_below:", ns(ps))
    ps = [P(1), P(2)]
    dispatched(ps)
    print("dispatch:", ns(ps))
    ps = [P(1), P(2)]
    receiver(ps)
    print("receiver:", ns(ps))
    ps = [P(1), P(2)]
    alias_receiver(ps)
    print("alias_receiver:", ns(ps))
    ps = [P(1), P(2)]
    nested_receiver(ps)
    print("nested_receiver:", ns(ps))
    ps = [P(1), P(2)]
    closure_alias_receiver(ps)
    print("closure_alias_receiver:", ns(ps))
    ps = [P(1), P(2)]
    closure_alias_arg(ps)
    print("closure_alias_arg:", ns(ps))
    rows = [[P(1)], [P(2), P(3)]]
    outer_alias_nested(rows)
    print("outer_alias_nested:", ns(rows[0]), ns(rows[1]))
    print("local_source:", local_source())
    n = P(0)
    ms = [P(1), P(2)]
    print("comp_shadow:", comp_shadow(n, ms), n.n, ns(ms))
    alias_src = P(1)
    qs3 = [P(5)]
    comp_shadow_alias(alias_src, qs3)
    print("comp_shadow_alias after:", alias_src.n, ns(qs3))
    ps3 = [P(1), P(2)]
    loop_shadow_alias(ps3, [P(5)])
    print("loop_shadow_alias after:", ns(ps3))
    ros = [H(1)]
    others = [H(2)]
    m = H(3)
    s = reused_loop_name(ros, others, m)
    print("reused_loop_name:", s, ros[0].bp[0].n, others[0].bp[0].n, m.bp[0].n)
    ros = [H(1)]
    others = [H(2)]
    join_loop_name(True, ros, others, m)
    join_loop_name(False, ros, others, m)
    print("join_loop_name:", ros[0].bp[0].n, others[0].bp[0].n, m.bp[0].n)
    ros = [H(1)]
    others = [H(2)]
    try_rebound(0, ros, others, m)
    try_rebound(1, ros, others, m)
    print("try_rebound:", ros[0].bp[0].n, others[0].bp[0].n, m.bp[0].n)

    ps = [P(1), P(2)]
    print("read_only:", read_only(ps), ns(ps))
    print("const_receiver:", const_receiver(ps), ns(ps))
    print("const_alias_receiver:", const_alias_receiver(ps), ns(ps))
    print("const_nested_receiver:", const_nested_receiver(ps), ns(ps))
    qs2 = [Q(), Q()]
    auto_readonly_alias(qs2)
    print("auto_readonly:", len(qs2[0].xs), len(qs2[1].xs))
    ps = [P(1), P(2)]
    closure_rebound_capture(ps)
    print("closure_rebound_capture:", ns(ps))

    print("aug_owned_local:", aug_owned_local())
    print("aug_nonlocal_owned:", aug_nonlocal_owned())
    print("aug_nonlocal_value:", aug_nonlocal_value())
    ia = [IAcc(1), IAcc(2)]
    aug_nonlocal_iadd(ia)
    print("aug_nonlocal_iadd:", ia[0].n, ia[1].n)
    xs = [IAcc(1), IAcc(2)]
    aug_iadd_zip(xs, [10, 20])
    print("aug_iadd_zip:", xs[0].n, xs[1].n)
    vs = [Acc(1), Acc(2)]
    rebind_zip(vs, [10, 20])
    print("rebind_zip:", vs[0].n, vs[1].n)


main()
