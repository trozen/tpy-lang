# An Own[P | None] (or whole Own[tuple[P | None, ...]]) call-result unpack
# target borrows the callee's object from the held capture, so it aliases.
from typing import Callable
from tpy import Own, int32, error_return, ReturnException


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


class Q:
    def __init__(self, n: int32) -> None:
        self.n = n


class Bad(Exception, ReturnException):
    pass


def mko(i: int32) -> tuple[Own[P | None], int32]:
    return (P(i), i)


def mk2(i: int32) -> tuple[Own[P | None], Own[P | None]]:
    return (P(i), P(i + 1))


def mkr(i: int32) -> tuple[int32, Own[P | None]]:
    return (i, P(i))


def mk3(i: int32) -> tuple[Own[P | None], Own[P], int32]:
    return (P(i), P(i + 1), i)


def mkw(i: int32) -> Own[tuple[P | None, int32]]:
    return (P(i), i)


def mkw0(i: int32) -> Own[tuple[P | None, int32]]:
    return (None, i)


def mkp(i: int32) -> tuple[Own[P], int32]:
    return (P(i), i)


def mkq(i: int32) -> tuple[Own[P | Q | None], int32]:
    return (P(i), i)


def show(tag: str, p: P | None) -> None:
    if p is None:
        print(tag, "None")
    else:
        print(tag, p.n)


def bump(p: P | None) -> None:
    if p is not None:
        p.n += 100


# Own only to force the move out of the caller's held capture; the body just
# mutates, so the never-consumed warning is expected.
def take(p: Own[P | None]) -> None:  # tpyc: warning(/never consumed/)
    if p is not None:
        p.n += 100
        print("take", p.n)


class H:
    o: P | None

    def __init__(self, i: int32) -> None:
        # ctor: the unpack in a constructor body; storing its owned element
        # at its last use moves it.
        o, k = mko(i)  # tpyc: ok
        keep = o
        filler = [P(9), P(9), P(9)]
        if o is not None:
            o.n += 10
        show("ctor-keep", keep)
        self.o = o  # tpyc: ok
        print("ctor", k, len(filler))

    def mko(self, i: int32) -> tuple[Own[P | None], int32]:
        return (P(i), i)

    def run(self) -> None:
        # method: the unpack in a method body.
        o, k = mko(2)  # tpyc: ok
        keep = o
        filler = [P(9), P(9), P(9)]
        if o is not None:
            o.n += 10
        show("method-keep", keep)
        bump(o)
        show("method", o)
        print("method", k, len(filler))


def free_fn() -> None:
    # free: the unpack in a free function.
    o, k = mko(1)  # tpyc: ok
    keep = o
    filler = [P(9), P(9), P(9)]
    if o is not None:
        o.n += 10
    show("free-keep", keep)
    bump(o)
    show("free", o)
    print("free", k, len(filler))


def closure() -> None:
    def inner(i: int32) -> None:
        # closure: the unpack in a nested def.
        o, k = mko(i)  # tpyc: ok
        keep = o
        filler = [P(9), P(9), P(9)]
        if o is not None:
            o.n += 10
        show("closure-keep", keep)
        bump(o)
        show("closure", o)
        print("closure", k, len(filler))
    inner(3)


def match_arm(sel: int32) -> None:
    match sel:
        case 1:
            # match: the unpack in a match arm.
            o, k = mko(4)  # tpyc: ok
            keep = o
            filler = [P(9), P(9), P(9)]
            if o is not None:
                o.n += 10
            show("match-keep", keep)
            bump(o)
            show("match", o)
            print("match", k, len(filler))
        case _:
            print("match other")


@error_return(Bad)
def err_body(x: int32) -> int32:
    if x < 0:
        raise Bad
    # errret: the unpack in an @error_return body.
    o, k = mko(5)  # tpyc: ok
    keep = o
    filler = [P(9), P(9), P(9)]
    if o is not None:
        o.n += 10
    show("errret-keep", keep)
    bump(o)
    show("errret", o)
    return k + len(filler)


def for_body() -> None:
    for i in range(2):
        # for: the unpack once per iteration.
        o, k = mko(i)  # tpyc: ok
        keep = o
        filler = [P(9), P(9), P(9)]
        if o is not None:
            o.n += 10
        show("for-keep", keep)
        bump(o)
        show("for", o)
        print("for", k, len(filler))


def method_source() -> None:
    h = H(0)
    # methsrc: a method-call source.
    o, k = h.mko(6)  # tpyc: ok
    keep = o
    filler = [P(9), P(9), P(9)]
    if o is not None:
        o.n += 10
    show("methsrc-keep", keep)
    bump(o)
    show("methsrc", o)
    print("methsrc", k, len(filler))


def two_optionals() -> None:
    # twoopt: two Optional elements.
    a, b = mk2(7)  # tpyc: ok
    ka = a
    kb = b
    filler = [P(9), P(9), P(9)]
    if a is not None:
        a.n += 10
    if b is not None:
        b.n += 20
    show("twoopt-keep-a", ka)
    show("twoopt-keep-b", kb)
    bump(a)
    bump(b)
    show("twoopt-a", a)
    show("twoopt-b", b)
    print("twoopt", len(filler))


def later_position() -> None:
    # nonzero: the Optional element at index 1.
    k, o = mkr(8)  # tpyc: ok
    keep = o
    filler = [P(9), P(9), P(9)]
    if o is not None:
        o.n += 10
    show("nonzero-keep", keep)
    bump(o)
    show("nonzero", o)
    print("nonzero", k, len(filler))


def discarded() -> None:
    # discard: the Optional element discarded, then the scalar.
    _, k = mko(9)  # tpyc: ok
    o, _ = mko(10)  # tpyc: ok
    keep = o
    filler = [P(9), P(9), P(9)]
    if o is not None:
        o.n += 10
    show("discard-keep", keep)
    bump(o)
    show("discard", o)
    print("discard", k, len(filler))


def mixed() -> None:
    # mixed: an Own[P | None] element beside an Own[P] one.
    o, p, k = mk3(11)  # tpyc: ok
    keep = o
    filler = [P(9), P(9), P(9)]
    p.n += 20
    if o is not None:
        o.n += 10
    show("mixed-keep", keep)
    bump(o)
    show("mixed", o)
    print("mixed", p.n, k, len(filler))


def whole_own() -> None:
    # whole: an Own[tuple[P | None, int32]] result.
    w, k = mkw(12)  # tpyc: ok
    keep = w
    filler = [P(9), P(9), P(9)]
    if w is not None:
        w.n += 10
    show("whole-keep", keep)
    bump(w)
    show("whole", w)
    print("whole", k, len(filler))


def pass_owned() -> None:
    # take: the target moves out of the capture into an Own[P | None] param.
    o, k = mko(16)  # tpyc: ok
    filler = [P(9), P(9), P(9)]
    if o is not None:
        o.n += 10
    take(o)
    print("take", k, len(filler))


def whole_empty() -> None:
    # wholeempty: an empty Optional through a whole-Own return.
    e, k = mkw0(17)  # tpyc: ok
    filler = [P(9), P(9), P(9)]
    print("wholeempty", e is None, k, len(filler))


def callable_source(f: Callable[[int32], tuple[Own[P | None], int32]]) -> None:
    # cbsrc: a Callable-parameter source.
    o, k = f(18)  # tpyc: ok
    keep = o
    filler = [P(9), P(9), P(9)]
    if o is not None:
        o.n += 10
    show("cbsrc-keep", keep)
    bump(o)
    show("cbsrc", o)
    print("cbsrc", k, len(filler))


def own_record() -> None:
    # ownrec (inverse): an Own[P] element moves out of the capture.
    a, k = mkp(14)  # tpyc: ok
    keep = a
    filler = [P(9), P(9), P(9)]
    a.n += 10
    print("ownrec-keep", keep.n)
    bump(a)
    print("ownrec", a.n, k, len(filler))


def own_union() -> None:
    # pqnone (inverse): an Own[P | Q | None] element.
    u, k = mkq(15)  # tpyc: ok
    keep = u
    filler = [P(9), P(9), P(9)]
    if isinstance(u, P):
        u.n += 10
    if isinstance(keep, P):
        print("pqnone-keep", keep.n)
    print("pqnone", k, len(filler))


def main() -> None:
    free_fn()
    h = H(0)
    show("ctor-field", h.o)
    h.run()
    closure()
    match_arm(1)
    try:
        r = err_body(1)
    except Bad:
        print("errret bad")
    else:
        print("errret", r)
    for_body()
    method_source()
    two_optionals()
    later_position()
    discarded()
    mixed()
    whole_own()
    pass_owned()
    whole_empty()
    callable_source(mko)
    own_record()
    own_union()


main()
