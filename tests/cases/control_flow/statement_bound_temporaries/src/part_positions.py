# The statement-bound operands at the other synchronous positions: a
# method, a constructor, comprehensions (a dict comprehension's filter and
# an `Array` comprehension over an `Array` included), an `@error_return`
# body, a match guard, `elif` / `while` / `assert` conditions, a `with`
# item and body, a `try` / `finally` body.
from typing import Iterator
from tpy import int32, error_return, ReturnException, Array, Own, StrView
from fixtures import P, head, mk, show, side, total


class Runner:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def run(self, flag: bool) -> None:
        # Method: a view pack over a fresh string and a guarded select.
        show("method", self.tag, self.tag + "-tail-long-heap-string-0123456789")  # tpyc: ok
        method_guard_r = flag and (side("method", 0).n or 5) > 0  # tpyc: ok
        print("method-guard", method_guard_r)
        method_in_r = mk("method", 1).ps[0].n in (1, 2)  # tpyc: ok
        print("method-in", method_in_r)


def comprehensions(name: str) -> None:
    # Comprehension element and filter: each iteration's temporaries.
    xs = [total(name, name + str(i)) for i in range(2)]  # tpyc: ok
    print("comp", xs)
    ys = [i for i in range(3) if 0 < mk("comp", i).p.n < 2]  # tpyc: ok
    print("comp-filter", ys)
    # A dict comprehension's filter holds a select operand.
    d = {i: i * 2 for i in range(3) if (side("dict", i).n or 9) != 1}  # tpyc: ok
    print("dict-filter", d)


class E(Exception, ReturnException):
    pass


@error_return(E)
def er_val(n: int32) -> int32:
    if n < 0:
        raise E()
    return n


@error_return(E)
def checked(name: str, n: int32) -> int32:
    if n < 0:
        raise E()
    # A guarded held operand whose call propagates an error: built in its
    # arm, where the propagation can still leave the body (n == 1 raises
    # there, n == 0 skips it).
    ok = n > 0 and (er_val(n - 2) or 4) > 1  # tpyc: ok
    print("er-guard", ok)
    # `@error_return` body: a pack and a chain over a fresh root.
    t = total(name, name + "-er-long-heap-string-0123456789")  # tpyc: ok
    if 0 < mk("er", n).p.n < 9:  # tpyc: ok
        return t
    return 0


def guard(q: int32) -> int32:
    match q:
        case 1:
            return 1
        # Match guard: a held select operand, then a chain, each reached
        # only when the earlier guard failed.
        case x if (mk("guard-sel", 0).p.n or 7) == x:  # tpyc: ok
            return 2
        case x if 0 < mk("guard-chain", 5).p.n < x:  # tpyc: ok
            return 3
        case _:
            return 4


def conditions(q: int32, name: str) -> int32:
    if q == 0:
        return 0
    # `elif` conditions: each operand runs only when its arm is tested.
    elif (mk("elif-sel", 0).p.n or 7) == q:  # tpyc: ok
        return 1
    elif total(name, name + "-tail") == q:  # tpyc: ok
        return 2
    elif mk("elif-in", 3).name.strip() in ("a", "b"):  # tpyc: ok
        return 3
    return 5


def bump_pair(p: tuple[P, P]) -> Iterator[int32]:
    yield p[0].n
    p[1].n += 1
    yield p[1].n


def loops(name: str) -> None:
    n = 0
    # `while` condition: a held operand over a fresh root, per iteration.
    while n < 3 and (side("while", n).n or 1) < 3:  # tpyc: ok
        n += 1
    print("while", n)
    # `assert` condition: a held select operand over a fresh root.
    assert (mk("assert", 0).p.n or 7) > 1, name + "-msg"  # tpyc: ok
    print("assert ok")
    r = P(0)
    # A fresh tuple element a generator frame reads, seated before the
    # `assert` test; the frame writes the caller's object beside it.
    assert sum(bump_pair((P(3), r))) == 4  # tpyc: ok
    print("assert-tuple", r.n)


class Built:
    v: int32

    def __init__(self, g: bool) -> None:
        # Constructor body: the held operand runs only behind its guard.
        self.v = (side("ctor", 0).n or 5) if g else 0  # tpyc: ok


class Ctx:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        pass


def with_try(g: bool, name: str, boom: bool) -> None:
    # A `with` item and body.
    with Ctx((side("with-item", 0).n or 5) if g else 0) as v:  # tpyc: ok
        w = (side("with-body", 0).n or 6) if g else 0  # tpyc: ok
        show("with-pack", name, name + "-tail-long-heap-string-0123456789")  # tpyc: ok
        print("with", v, w)
    # A `try` body and its `finally`, left normally and by a raise (the
    # `finally` renders once per path).
    try:
        try:
            t = (side("try", 0).n or 7) if g else 0  # tpyc: ok
            print("try", t)
            if boom:
                raise ValueError("boom")
        finally:
            f = (side("finally", 0).n or 8) if g else 0  # tpyc: ok
            show("fin-pack", name, name + "-fin-long-heap-string-0123456789")  # tpyc: ok
            print("finally", f)
    except ValueError:
        print("try caught")


class D:
    s: str

    def __init__(self, n: int32) -> None:
        print("arrcomp init D", n)
        self.s = "d-long-heap-string-0123456789abcdef-" + str(n)

    def __del__(self) -> None:
        print("arrcomp del D", self.s)


def mkd(n: int32) -> Own[D]:
    return D(n)


def array_source(c: bool) -> None:
    s = "w-long-heap-string-0123456789abcdef"
    arr: Array[int32, 2] = [1, 2]
    # An `Array` comprehension over an `Array`: each index builds and
    # destroys its own seated root and its own held operand.
    xs: Array[bool, 2] = [(head(mkd(i).s) if c else head(s)) in ("d-long-heap-string-0123456789abcdef", "o") for i in arr]  # tpyc: ok
    zs: Array[int32, 2] = [((i + 1 or 5) if c else 0) for i in arr]  # tpyc: ok
    print("arrcomp", c, xs, zs)
    pairs: Array[tuple[int32, int32], 2] = [(1, 2), (3, 4)]
    # The same with an unpacked target.
    us: Array[bool, 2] = [(head(mkd(a + b).s) if c else head(s)) in ("d-long-heap-string-0123456789abcdef", "o") for a, b in pairs]  # tpyc: ok
    print("arrcomp-unpack", c, us)


def main() -> None:
    Runner("rn").run(True)
    Runner("rn").run(False)
    comprehensions("cn")
    for n in [2, 1, 0]:
        try:
            er = checked("er", n)
            print("error_return", n, er)
        except E:
            print("error_return raised", n)
    for q in [1, 7, 43, 5]:
        g = guard(q)
        print("guard", q, g)
    for q, nm in [(0, "nn"), (7, "nn"), (11, "nnnnn"), (9, "nn")]:
        c = conditions(q, nm)
        print("elif", q, c)
    loops("w")
    for g in [True, False]:
        b = Built(g)
        print("ctor", b.v)
        with_try(g, "ab", False)
        with_try(g, "ab", True)
        array_source(g)
