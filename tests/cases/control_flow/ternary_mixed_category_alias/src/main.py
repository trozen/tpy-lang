# A record / container ternary whose arms mix an existing object and a fresh
# value ALIASES the existing arm, like CPython: the fresh arm is built into a
# hoisted slot only when chosen. Owning sinks copy the chosen arm and warn;
# their sections print only the owned side, since the copy is the warned
# divergence from CPython's alias.
import asyncio
from typing import Callable, Iterator
from tpy import int32, Own, copy


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1

    def get(self) -> int32:
        return self.n

    def inc(self) -> int32:
        self.n += 1
        return self.n

    def __bool__(self) -> bool:
        # A zero `C` is falsy, so the and/or sections take their fresh
        # operand too.
        return self.n != 0


def make(log: list[str]) -> Own[C]:
    log.append("make")
    return C(10)


def make_ba(log: list[str]) -> Own[bytearray]:
    log.append("make")
    return bytearray(b"z")


def take(x: Own[C]) -> Own[C]:
    return x


def take_list(xs: Own[list[int32]]) -> Own[list[int32]]:
    return xs


def give_or(a: C) -> Own[C]:
    # Own return of an and/or select: the chosen `a` is copied, and warned.
    return a or C(2)  # tpyc: warning(/copies C into owned storage/)


def give(a: C, c: bool, log: list[str]) -> Own[C]:
    # Own return: the chosen `a` is copied into the returned value.
    return a if c else make(log)  # tpyc: warning(/copies C into owned storage/)


class Holder:
    a: C

    def __init__(self) -> None:
        self.a = C(1)

    def alias_bump(self, c: bool) -> None:
        a = self.a
        # A select operand that aliases a `self` field is credited to `self`:
        # the method is not inferred readonly.
        (a if c else C(0)).bump()  # tpyc: ok

    def total(self) -> int32:
        return self.a.n

    def peek(self, other: "Holder", c: bool) -> int32:
        # A `self` operand of a non-mutating call keeps readonly inference
        # (the method stays const).
        return (self if c else other).total()  # tpyc: ok

    def meth(self, c: bool, log: list[str]) -> int32:
        # A field arm is not lowered yet
        # (BUGS.md#reference-ternary-position-gaps), so bind it first.
        a = self.a
        # Method body: the local aliases the field's object.
        x = a if c else make(log)  # tpyc: ok
        x.bump()
        return self.a.n


class Keep:
    f: C

    def __init__(self, a: C, c: bool, log: list[str]) -> None:
        # Constructor member-init: the field stores a copy of the chosen arm.
        self.f = a if c else make(log)  # tpyc: warning(/copies C into field/)


class KeepNames:
    f: C

    def __init__(self, a: C, b: C, c: bool) -> None:
        # Member-init from two names: the field stores a copy either way.
        self.f = a if c else b  # tpyc: warning(/copies C into field/)


class KeepElem:
    f: C

    def __init__(self, rs: list[C], c: bool) -> None:
        # An element arm stores a copy too, and says so.
        self.f = rs[0] if c else C(9)  # tpyc: warning(/copies C into field/)


def ctor_init(c: bool) -> None:
    log: list[str] = []
    k = Keep(C(1), c, log)
    kn = KeepNames(C(1), C(2), c)
    ke = KeepElem([C(3)], c)
    print("ctor_init", c, k.f.n, kn.f.n, ke.f.n, len(log))


def local_decl(c: bool) -> None:
    log: list[str] = []
    a = C(1)
    # Local binding: aliases `a`; make() runs only when chosen.
    x = a if c else make(log)  # tpyc: ok
    x.bump()
    print("decl", c, a.n, x.n, len(log))


def ctor_arm(c: bool) -> None:
    a = C(1)
    # A ctor as the fresh arm, written first.
    x = C(10) if not c else a  # tpyc: ok
    x.bump()
    print("ctor", c, a.n, x.n)


def elem_arm(c: bool) -> None:
    rs = [C(1)]
    # A container-element arm beside a ctor arm aliases the element.
    r = rs[0] if c else C(9)  # tpyc: ok
    r.n += 1
    print("elem", c, rs[0].n, r.n)


def list_ctor_arm(c: bool) -> None:
    rs = [[1, 2]]
    # A builtin container ctor is the fresh arm beside an element arm.
    x = rs[0] if c else list(range(3))  # tpyc: ok
    x.append(7)
    print("list_ctor", c, len(rs[0]), len(x))


def list_literal_arm(c: bool) -> None:
    a = [1, 2, 3]
    # A list literal as the fresh arm spells its type.
    ys = a if c else [9]  # tpyc: ok
    ys.append(7)
    print("list", c, len(a), len(ys))


def receiver(c: bool) -> None:
    log: list[str] = []
    a = C(1)
    # Method receiver: the mutation lands on `a`, not on a copy.
    (a if c else make(log)).bump()  # tpyc: ok
    print("recv", c, a.n, len(log))


def container_receiver(c: bool) -> None:
    log: list[str] = []
    a = bytearray(b"q")
    # Container receiver: the append lands on `a`.
    (a if c else make_ba(log)).append(1)  # tpyc: ok
    print("recv_ba", c, len(a), len(log))


def own_arg(c: bool) -> None:
    log: list[str] = []
    a = C(1)
    # Own parameter: the chosen `a` is copied into the owned slot.
    print("own_arg", c, take(a if c else make(log)).n, len(log))  # tpyc: warning(/copies C into owned storage/)
    # The acknowledged copy: `copy(a)` makes both arms fresh.
    print("own_arg_copy", c, take(copy(a) if c else make(log)).n, len(log))  # tpyc: ok


def append_arg(c: bool) -> None:
    log: list[str] = []
    a = C(1)
    xs = [C(0)]
    # Container insert: the element is a copy of the chosen arm.
    xs.append(a if c else make(log))  # tpyc: warning(/copies C into owned storage/)
    print("append", c, xs[1].n, len(log))
    # The acknowledged copy: the chosen arm is built straight into the list,
    # and a mutation through the element leaves `a` alone.
    xs.append(copy(a) if c else make(log))  # tpyc: ok
    xs[2].bump()
    print("append_copy", c, xs[2].n, a.n, len(log))


def own_return(c: bool) -> None:
    log: list[str] = []
    a = C(1)
    r = give(a, c, log)
    print("return", c, r.n, len(log))


def fresh_container_arms(c: bool) -> None:
    a = [1]
    d = {"k": 1}
    s = {1}
    # Fresh container arms of every lowered shape: an operator result, a
    # dict literal, a set literal, a list comprehension.
    ys = a if c else a + [9]  # tpyc: ok
    ys.append(2)
    e = d if c else {"z": 2}  # tpyc: ok
    e["n"] = 3
    t = s if c else {7, 8}  # tpyc: ok
    t.add(5)
    zs = a if c else [i for i in range(3)]  # tpyc: ok
    zs.append(9)
    print("fresh", c, len(a), len(ys), len(d), len(e), len(s), len(t), len(zs))


def nested_select_arm(c: bool, e: list[int32]) -> None:
    a = [1]
    b = [2]
    # A select as the existing-object arm is an lvalue too.
    zs = (a or b) if c else [5]  # tpyc: ok
    zs.append(3)
    # An and/or with a fresh operand as an arm takes the outer select's slot
    # admission.
    ws = (e or [5]) if c else b  # tpyc: ok
    ws.append(4)
    print("nested", c, len(a), len(zs), len(b), len(e), len(ws))


def branch_positions(a: C, k: int32) -> None:
    log: list[str] = []
    xs: list[int32] = [0, 0, 0, 0]
    # An `elif` condition and an augmented assignment's value lower the
    # select in place, the fresh operand only when chosen.
    if k == 0:
        pass
    elif (a if k > 1 else make(log)).inc() > 5:  # tpyc: ok
        xs[0] = 1
    xs[1] += (a or C(3)).inc()  # tpyc: ok
    print("branch", k, a.n, xs[0], xs[1], len(log))


def or_positions(a: C) -> None:
    # `copy()` of an and/or with a fresh operand: the slot, then the copy.
    x = copy(a or C(2))  # tpyc: ok
    x.bump()
    k = 0
    # A `while` condition re-evaluates the select each iteration.
    while (a or C(0)).n + k < 4:  # tpyc: ok
        k += 1
    print("or_pos", a.n, x.n, k)


async def async_own_sel(a: C, c: bool) -> Own[C]:
    # Async Own return of a mixed ternary: the prvalue `?:`, the chosen `a`
    # copied and warned.
    return a if c else C(50)  # tpyc: warning(/copies C into owned storage/)


def copy_ternary(c: bool) -> None:
    log: list[str] = []
    a = C(1)
    # `copy()` of the select owns a copy of the chosen arm.
    x = copy(a if c else make(log))  # tpyc: ok
    x.bump()
    print("copy", c, a.n, x.n, len(log))


def own_list_arg(c: bool) -> None:
    a = [1]
    # A container at an `Own` parameter: the chosen `a` is copied.
    print("own_list", c, len(take_list(a if c else [9, 9])))  # tpyc: warning(/copies list\[int32\] into owned storage/)


def match_arm(k: int32, c: bool) -> None:
    log: list[str] = []
    a = C(1)
    match k:
        case 1:
            # Match arm: the binding aliases `a` or holds the fresh value.
            x = a if c else make(log)  # tpyc: ok
            x.bump()
            print("match", k, c, a.n, x.n, len(log))
        case _:
            pass


def param_arms(a: C, rs: list[C], xs: list[int32], ys: list[int32],
               c: bool) -> None:
    # A mutating call through a select credits every arm's root, so the
    # params are not const: a param arm, an element arm, an and/or arm.
    (a if c else C(9)).bump()  # tpyc: ok
    (rs[0] if c else C(9)).bump()  # tpyc: ok
    ((xs or ys) if c else [5]).append(1)  # tpyc: ok
    (a or C(7)).bump()  # tpyc: ok
    for r in rs:
        (r if c else C(9)).bump()  # tpyc: ok


def run_param_arms(c: bool) -> None:
    a = C(1)
    rs = [C(10)]
    xs = [1]
    print("params", c, a.n, rs[0].n, len(xs))
    param_arms(a, rs, xs, [2], c)
    print("params", c, a.n, rs[0].n, len(xs))


def dict_elem_arm(c: bool) -> None:
    d = {"k": [1]}
    # A dict-element arm aliases the stored list.
    ys = d["k"] if c else [9]  # tpyc: ok
    ys.append(2)
    print("dict_elem", c, len(d["k"]), len(ys))


def gen_receiver(a: C, c: bool) -> Iterator[int32]:
    log: list[str] = []
    # Generator body: a select receiver whose result is a value; the
    # fresh operand's slot lives within the statement.
    yield (a if c else make(log)).inc()  # tpyc: ok
    yield (a or C(5)).inc()  # tpyc: ok


async def async_receiver(a: C, c: bool) -> int32:
    log: list[str] = []
    # Async body: a select receiver whose result is a value, like the
    # generator section.
    first = (a if c else make(log)).inc()  # tpyc: ok
    second = (a or C(5)).inc()  # tpyc: ok
    return first * 100 + second


def str_receivers(s: str, c: bool) -> None:
    # A value-type select never takes a slot, so a fresh operand is fine as
    # a receiver.
    print("str_recv", (s or "dflt").upper(), (s if c else "zz").startswith("z"))  # tpyc: ok


def first_list(xs: list[list[int32]]) -> list[int32]:
    return xs[0]


def escaping_aliases(a: list[int32],
                     xs: list[list[int32]]) -> Callable[[], int32]:
    y = a
    z = first_list(xs)
    w = a or [5]

    # Plain, borrow-call and and/or aliases are copied into the closure too,
    # never moved out of their source.
    def inner() -> int32:  # tpyc: warning(/copies local 'y', which aliases storage/) warning(/copies local 'z', which aliases storage/) warning(/copies local 'w', which aliases storage/)
        return len(y) + len(z) + len(w)
    return inner


def escaping_closure(a: list[int32], c: bool) -> Callable[[], int32]:
    x = a if c else [5, 5]

    # Escaping closure: the select alias is copied into the closure, never
    # moved out of `a` (the copy is the warned divergence, so the output
    # reads it before `a` changes).
    def inner() -> int32:  # tpyc: warning(/copies local 'x', which aliases storage/)
        return len(x)
    return inner


def loop_body() -> None:
    log: list[str] = []
    a = C(1)
    for i in range(3):
        # Loop body: each iteration binds its own selection.
        x = a if i < 2 else make(log)  # tpyc: ok
        x.bump()
        print("loop", i, x.n)
    print("loop_a", a.n, len(log))


def comprehension() -> None:
    log: list[str] = []
    a = C(1)
    # Comprehension element: the mutating receiver is `a` on the first
    # element, the fresh value on the second.
    ys = [(a if i == 0 else make(log)).inc() for i in range(2)]  # tpyc: ok
    print("comp", ys, a.n, len(log))


def closure() -> None:
    log: list[str] = []
    a = C(1)

    def inner(c: bool) -> int32:
        # Closure body: the captured `a` is the aliased arm.
        x = a if c else make(log)  # tpyc: ok
        x.bump()
        return x.n
    print("closure", inner(True), inner(False), a.n, len(log))


def main() -> None:
    for c in [True, False]:
        local_decl(c)
        ctor_arm(c)
        elem_arm(c)
        list_ctor_arm(c)
        list_literal_arm(c)
        receiver(c)
        container_receiver(c)
        own_arg(c)
        append_arg(c)
        own_return(c)
        ctor_init(c)
        fresh_container_arms(c)
        nested_select_arm(c, [])
        nested_select_arm(c, [8])
        copy_ternary(c)
        own_list_arg(c)
    match_arm(1, True)
    match_arm(1, False)
    run_param_arms(True)
    run_param_arms(False)
    dict_elem_arm(True)
    dict_elem_arm(False)
    h2 = Holder()
    h2.alias_bump(True)
    h2.alias_bump(False)
    o9 = Holder()
    print("alias_bump", h2.a.n, h2.peek(o9, True), h2.peek(o9, False))
    str_receivers("", True)
    str_receivers("ab", False)
    ea = [1]
    exs = [[1, 2]]
    print("closure_aliases", escaping_aliases(ea, exs)(), len(ea), len(exs[0]))
    ca = [1, 2, 3]
    print("closure_escape", escaping_closure(ca, True)(), len(ca))
    print("closure_escape", escaping_closure(ca, False)(), len(ca))
    for gv in [1, 0]:
        ra = C(gv)
        r = give_or(ra)
        r.bump()
        print("give_or", gv, r.n)
    for ov in [1, 0]:
        oa = C(ov)
        or_positions(oa)
        ao = asyncio.run(async_own_sel(oa, ov != 0))
        ao.bump()
        print("async_own_sel", ov, ao.n)
    aa = C(1)
    print("async", asyncio.run(async_receiver(aa, True)), aa.n)
    print("async", asyncio.run(async_receiver(aa, False)), aa.n)
    for bk in [1, 2]:
        branch_positions(C(0), bk)
        branch_positions(C(6), bk)
    za = C(0)
    print("async_zero", asyncio.run(async_receiver(za, False)), za.n)
    zg = C(0)
    print("gen_zero", [v for v in gen_receiver(zg, False)], zg.n)
    ga = C(1)
    print("gen", [v for v in gen_receiver(ga, True)], ga.n)
    print("gen", [v for v in gen_receiver(ga, False)], ga.n)
    loop_body()
    comprehension()
    closure()
    h = Holder()
    log: list[str] = []
    print("meth", h.meth(True, log), h.meth(False, log), len(log))


main()
