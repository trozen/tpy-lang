# An all-fresh record / container select initializes each owning slot directly
# (no slot, no copy); each section mutates what it bound to expose a stray alias.
import asyncio
from typing import Iterator
from tpy import int32, Own, copy, nocopy


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __bool__(self) -> bool:
        # A zero `C` is falsy, so an and/or over one takes its right operand.
        return self.n != 0


@nocopy
class N:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make(log: list[str]) -> Own[C]:
    log.append("make")
    return C(10)


def fresh() -> Own[C]:
    return C(20)


def take(x: Own[C]) -> Own[C]:
    x.n += 100
    return x


def local_decl(c: bool, log: list[str]) -> None:
    a = C(1)
    # Local from two constructors.
    z = C(3) if c else C(6)  # tpyc: ok
    z.n += 1
    # Local from an explicit copy or a factory: `a` stays untouched.
    w = copy(a) if c else make(log)  # tpyc: ok
    w.n += 1
    # A nested all-fresh select is itself a fresh arm.
    v = C(4) if c else (C(5) if z.n > 5 else make(log))  # tpyc: ok
    v.n += 1
    # A container local.
    xs = [1] if c else [2, 3]  # tpyc: ok
    xs.append(9)
    print("local_decl", c, z.n, w.n, v.n, a.n, xs, len(log))


def loop_body(c: bool, log: list[str]) -> None:
    a = C(1)
    for i in range(2):
        # Loop-body local: a fresh value each iteration.
        z = copy(a) if c else make(log)  # tpyc: ok
        z.n += i
        print("loop_body", c, i, z.n, a.n)


def branch_first(c: bool, d: bool) -> None:
    if c:
        # Branch-first local, used only inside its branch.
        z = C(1) if d else C(2)  # tpyc: ok
        z.n += 1
        print("branch_first", d, z.n)


def reassigned(c: bool, d: bool, log: list[str]) -> None:
    # A reassigned local: the first select fills the rebind slot, the second
    # overwrites it.
    z = C(1) if c else C(2)  # tpyc: ok
    z.n += 1
    first = z.n
    z = C(3) if d else make(log)  # tpyc: ok
    z.n += 1
    print("reassigned", c, d, first, z.n)


def hoisted(c: bool, d: bool) -> None:
    if c:
        y = C(1)
    else:
        # Bound in a branch, read after it.
        y = C(2) if d else C(3)  # tpyc: ok
    y.n += 1
    print("hoisted", c, d, y.n)


def give(a: C, c: bool, log: list[str]) -> Own[C]:
    # Own return of an all-fresh select.
    return copy(a) if c else make(log)  # tpyc: ok


def give_list(c: bool) -> Own[list[int32]]:
    # ... and of a container one.
    return [1] if c else [2]  # tpyc: ok


def own_return(c: bool, log: list[str]) -> None:
    a = C(1)
    r = give(a, c, log)
    r.n += 1
    xs = give_list(c)
    xs.append(3)
    print("own_return", c, r.n, a.n, xs)


def or_local(log: list[str]) -> None:
    # Owned local of a fresh and/or: the falsy LHS is dropped, the factory
    # runs.
    z = C(0) or make(log)  # tpyc: ok
    z.n += 1
    # A truthy fresh LHS is moved into the local; the factory never runs.
    y = C(7) or make(log)  # tpyc: ok
    y.n += 1
    # Container and/or of two literals.
    s = {1, 2} or {3, 4}  # tpyc: ok
    s.add(5)
    print("or_local", z.n, y.n, len(s), len(log))


def give_or_name(a: C) -> Own[C]:
    # A name LHS is copied into the owned result, and warned.
    return a or C(2)  # tpyc: warning(/copies C into owned storage/)


def give_or_fresh(log: list[str]) -> Own[C]:
    # Own return of an and/or of fresh operands.
    return C(0) or make(log)  # tpyc: ok


def give_or_list() -> Own[list[int32]]:
    # ... and of a container one.
    return [] or [2]  # tpyc: ok


def or_return(log: list[str]) -> None:
    # The falsy `C(0)` argument is not chosen, so no copy is observable.
    r = give_or_name(C(0))
    r.n += 1
    q = give_or_fresh(log)
    q.n += 1
    xs = give_or_list()
    xs.append(3)
    print("or_return", r.n, q.n, xs, len(log))


class Holder:
    f: C

    def __init__(self, c: bool, log: list[str]) -> None:
        # A ctor member-init from an all-fresh select.
        self.f = C(1) if c else make(log)  # tpyc: ok


def member_init(c: bool, log: list[str]) -> None:
    h = Holder(c, log)
    h.f.n += 1
    print("member_init", c, h.f.n, len(log))


def give_chain(a: C, b: C) -> Own[C]:
    # A nested name select LHS is copied into the owned result, and warned.
    return (a or b) or C(3)  # tpyc: warning(/copies C into owned storage/)


def or_chain() -> None:
    # Both names falsy: only the fresh side is chosen, so no copy is seen.
    r = give_chain(C(0), C(0))
    r.n += 1
    print("or_chain", r.n)


def own_arg(c: bool, d: bool, log: list[str]) -> None:
    a = C(1)
    # An all-fresh select binds the Own parameter directly.
    print("own_arg", c, take(copy(a) if c else make(log)).n, a.n)  # tpyc: ok
    # A nested all-fresh select.
    print("own_arg_nested", c, d, take(C(1) if c else (C(2) if d else make(log))).n)  # tpyc: ok
    # A name LHS is copied into the parameter, and warned; only the owned
    # side is printed, since the copy is the warned divergence.
    print("own_arg_or", c, take(a or C(5)).n)  # tpyc: warning(/copies C into owned storage/)


def give_nocopy(c: bool) -> Own[N]:
    # A @nocopy record: the prvalue select needs no copy.
    return N(1) if c else N(2)  # tpyc: ok


def nocopy_select(c: bool) -> None:
    z = N(3) if c else N(4)  # tpyc: ok
    z.n += 1
    r = give_nocopy(c)
    r.n += 1
    print("nocopy", c, z.n, r.n)


async def async_body(c: bool) -> Own[C]:
    await asyncio.sleep(0)
    # A frame local held across no suspension still direct-initializes.
    z = C(1) if c else fresh()  # tpyc: ok
    z.n += 1
    print("async_local", c, z.n)
    # Async Own return of an all-fresh select.
    return C(2) if c else fresh()  # tpyc: ok


def gen(c: bool) -> Iterator[int32]:
    # A generator frame local.
    z = C(1) if c else fresh()  # tpyc: ok
    yield z.n
    z.n += 5
    yield z.n


def main() -> None:
    log: list[str] = []
    for c in [True, False]:
        local_decl(c, log)
        loop_body(c, log)
        own_return(c, log)
        nocopy_select(c)
        member_init(c, log)
        for d in [True, False]:
            branch_first(c, d)
            reassigned(c, d, log)
            hoisted(c, d)
            own_arg(c, d, log)
        r = asyncio.run(async_body(c))
        r.n += 1
        print("async_return", c, r.n)
        for v in gen(c):
            print("gen", c, v)
    or_local(log)
    or_return(log)
    or_chain()


main()
