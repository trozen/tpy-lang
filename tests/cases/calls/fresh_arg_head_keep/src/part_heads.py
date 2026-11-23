# A compound head (`for`, comprehension, `with`, `match`) keeps its fresh
# arguments until the statement ends, in a plain function and in a generator.
from typing import Iterator
from tpy import Own, Ptr, int32


class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1, n + 2]

    def items(self) -> list[int32]:
        return self.xs


class Walk:
    _p: Ptr[Src]
    i: int32

    def __init__(self, s: Src) -> None:
        self._p = s
        self.i = 0

    def __iter__(self) -> "Walk":
        return self

    def __next__(self) -> int32:
        if self.i >= len(self._p.xs):
            raise StopIteration
        self.i += 1
        if self.i == 1:
            # the kept argument is written through while the loop runs
            self._p.xs.append(0)
        return self._p.xs[self.i - 1]

    def __enter__(self) -> "Walk":
        self._p.xs.append(9)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass

    def n(self) -> int32:
        return len(self._p.xs)


def pick(xs: list[int32]) -> list[int32]:
    return xs


def plain() -> None:
    # for head: the constructor's fresh argument and a borrowed literal
    for x in Walk(Src(100)):  # tpyc: ok
        print("head for", x)
    for y in pick([7, 8]):  # tpyc: ok
        print("head for literal", y)
    # for head: a constructed receiver
    for z in Src(50).items():  # tpyc: ok
        print("head for receiver", z)
    # comprehension clause source
    print("head comp", [c for c in Walk(Src(200))])  # tpyc: ok
    # with item
    with Walk(Src(300)) as w:  # tpyc: ok
        print("head with", w.n())
    # match subject
    match Walk(Src(400)):  # tpyc: ok
        case Walk() as m:
            print("head match", m.n())


def gen() -> Iterator[int32]:
    # the same heads in a generator: the arguments live on the frame
    for x in Walk(Src(500)):  # tpyc: ok
        yield x
    for y in pick([3, 4]):  # tpyc: ok
        yield y
    out = [c for c in Walk(Src(600))]  # tpyc: ok
    yield len(out)
    with Walk(Src(700)) as w:  # tpyc: ok
        yield w.n()


class Pin:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        print("frames make", n)
        self.xs = [n, n + 1]

    def items(self) -> list[int32]:
        return self.xs


class Hold:
    p: Ptr[Pin]

    def __init__(self, p: Pin) -> None:
        self.p = p

    def __enter__(self) -> "Hold":
        print("frames enter", self.p.xs[0])
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("frames exit", self.p.xs[0])


def walk(p: Pin) -> list[int32]:
    return p.xs


def first(t: tuple[Pin, int32]) -> list[int32]:
    return t[0].xs


def g_cond(c: bool, other: list[int32]) -> Iterator[int32]:
    # a conditional operand: filled on the frame only when its arm runs
    for x in pick(walk(Pin(30)) if c else other):  # tpyc: ok
        yield x


def g_recv(c: bool, other: list[int32]) -> Iterator[int32]:
    # a conditional fresh receiver
    for x in (Pin(40).items() if c else other):  # tpyc: ok
        yield x


def p_tuple() -> Own[list[int32]]:
    # the same tuple element in a plain loop: the statement's scope holds it
    out: list[int32] = []
    for x in first((Pin(55), 5)):  # tpyc: ok
        out.append(x)
    return out


def g_tuple() -> Iterator[int32]:
    # a tuple literal's fresh element
    for x in first((Pin(50), 5)):  # tpyc: ok
        yield x


def g_nosusp(c: bool, other: list[int32]) -> Iterator[int32]:
    # a loop with no suspension keeps its head in its own scope
    t = 0
    for x in pick(walk(Pin(60)) if c else other):  # tpyc: ok
        t += x
    yield t


def g_with() -> Iterator[int32]:
    # per item: make 1, enter 1, make 2, enter 2
    with Hold(Pin(1)) as a, Hold(Pin(2)) as b:  # tpyc: ok
        yield a.p.xs[0] + b.p.xs[0]


def g_match() -> Iterator[int32]:
    # a suspending match keeps its subject's fresh argument
    match Hold(Pin(70)):  # tpyc: ok
        case Hold() as h:
            yield h.p.xs[0]
            yield h.p.xs[1]


def g_value(xs: list[int]) -> Iterator[int]:
    # a value-typed fresh argument is passed as a value, never held
    for y in range(sum(xs), sum(xs) + 2):  # tpyc: ok
        yield y


def frames() -> None:
    # each generator runs before its print: its own lines come first
    cond, cond_off = drain(g_cond(True, [9])), drain(g_cond(False, [9]))
    print("frames cond", cond, cond_off)
    recv = drain(g_recv(True, [9]))
    print("frames recv", recv)
    tup, ptup = drain(g_tuple()), p_tuple()
    print("frames tuple", tup, ptup)
    nosusp = drain(g_nosusp(True, [9]))
    print("frames nosusp", nosusp)
    with_ = drain(g_with())
    print("frames with", with_)
    matched = drain(g_match())
    print("frames match", matched)
    value = list(g_value([1, 2]))
    print("frames value", value)

def drain(it: Iterator[int32]) -> Own[list[int32]]:
    # allocates between pulls, so storage a frame lost would be reused
    out: list[int32] = []
    for v in it:
        junk = [Src(k) for k in range(4)]
        out.append(v + len(junk) - 4)
    return out


def main() -> None:
    plain()
    print("head gen", drain(gen()))
    frames()
