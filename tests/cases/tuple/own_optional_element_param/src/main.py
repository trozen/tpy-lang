# An `Own[P] | None` tuple element is owned as a bare `Own[P]` one: the
# sections of tuple/own_optional_element_bare, nullable, plus None ones.
from typing import Callable
from tpy import Own, Send, copy, int32, nocopy, readonly


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def bump(self) -> None:
        self.x = 99


@nocopy
class Q:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


# free function, consuming unpack: t is not read after it
def consume(t: tuple[Own[P] | None, int32]) -> int32:
    a, n = t  # tpyc: ok
    if a is not None:
        a.bump()
        return a.x + n
    return n


# free function, non-consuming unpack: a later unpack sees the write
def borrow(t: tuple[Own[P] | None, int32]) -> int32:
    a, n = t  # tpyc: ok
    if a is not None:
        a.bump()
    c, m = t
    if c is not None:
        print("borrow same", a is c)
        return c.x
    return 0


# return of the first target after a re-unpack mutated the element
def ret_after(t: tuple[Own[P] | None, int32]) -> Own[P] | None:
    a, n = t
    c, m = t
    if c is not None:
        c.bump()
    return a  # tpyc: warning(/copies P( \| None)? into owned storage/)


# unpack in a loop body
def in_loop(t: tuple[Own[P] | None, int32]) -> int32:
    s = 0
    for i in range(2):
        a, n = t
        if a is not None:
            a.bump()
            s += a.x + n
    return s


# method and constructor storing the element into a field
class H:
    p: P | None

    def __init__(self, t: tuple[Own[P] | None, int32]) -> None:
        a, n = t
        self.p = a

    def put(self, t: tuple[Own[P] | None, int32]) -> None:
        a, n = t
        self.p = a


# match arm
def in_match(t: tuple[Own[P] | None, int32], k: int32) -> int32:
    match k:
        case 1:
            a, n = t
            if a is not None:
                a.bump()
                return a.x
        case _:
            pass
    return 0


# mixed: an owned element beside a borrowed one, written through
def mixed(t: tuple[Own[P] | None, P]) -> int32:
    a, b = t
    b.bump()
    if a is not None:
        return a.x + b.x
    return b.x


# two owned elements, the tuple re-read after the unpack
def two_owned(t: tuple[Own[P] | None, Own[P]]) -> int32:  # tpyc: warning(/owned tuple param 't' is never consumed/)
    a, b = t
    b.bump()
    print("two_owned alias", t[1].x, t[1] is b)
    return b.x


# rebind of a non-consuming target to a fresh value
def rebind_fresh(t: tuple[Own[P] | None, int32]) -> None:
    a, n = t
    a = P(9)
    b, m = t
    if b is not None:
        print("rebind_fresh", a.x, b.x)


# Optional only: rebind of a non-consuming target to None
def rebind_none(t: tuple[Own[P] | None, int32]) -> None:
    a, n = t
    a = None
    b, m = t
    print("rebind_none", a is None, b is None)

# rebind of a consuming target to a fresh value
def rebind_consumed_fresh(t: tuple[Own[P] | None, int32]) -> int32:
    a, n = t
    a = P(9)
    if a is not None:
        return a.x + n
    return n


# Optional only: a consuming target rebound to None
def rebind_consumed_none(t: tuple[Own[P] | None, int32]) -> int32:
    a, n = t
    a = None
    if a is None:
        return n
    return 0


# both elements unpacked and dropped: only the bare `Own[P]` one warns
def dropped(t: tuple[Own[P] | None, Own[P]]) -> int32:  # tpyc: warning(/element 1 of tuple param 't' is never consumed \(unpacked into 'b'/)
    a, b = t
    return b.x


# a mixed tuple forwarded whole to an identically typed parameter
def forward_mixed(t: tuple[Own[P] | None, P]) -> int32:
    return mixed(t)


# ... and before its last use: the owned element is copied, warned
def forward_mixed_again(t: tuple[Own[P] | None, P]) -> int32:
    r = mixed(t)  # tpyc: warning(/copies tuple\[Own\[P\]( \| None)?, P\] into owned storage/)
    return r + t[1].x



# readonly tuple, and a readonly payload
def ro_tuple(t: readonly[tuple[Own[P] | None, int32]]) -> int32:
    a, n = t
    c, m = t
    if a is not None:
        return a.x + n
    return n


def ro_payload(t: tuple[Own[readonly[P]] | None, int32]) -> int32:
    a, n = t
    c, m = t
    if c is not None:
        return c.x + m
    return m


# whole-tuple forward not at its last use, then an unpack
def sink(t: tuple[Own[P] | None, int32]) -> int32:
    a, n = t
    if a is not None:
        return a.x
    return 0


def forward_then_unpack(t: tuple[Own[P] | None, int32]) -> int32:
    s = sink(t)  # tpyc: warning(/copies tuple\[Own\[P\]( \| None)?, int32\] into owned storage/)
    a, n = t
    if a is not None:
        return s + a.x
    return s


# call-result unpack and literal unpack
def make(x: int32) -> tuple[Own[P] | None, int32]:
    return (P(x), x)


# a tuple literal returned at the owning result: a local moves in
def make_named(x: int32) -> tuple[Own[P] | None, int32]:
    p = P(x)
    return (p, x)


# a returned literal under try/finally: the finally's write reaches the result
def make_finally(x: int32) -> tuple[Own[P] | None, int32]:
    p = P(x)
    try:
        return (p, x)
    finally:
        p.bump()


# a borrowed parameter returned in one is copied, as the bare element is
def make_from(q: P) -> tuple[Own[P] | None, int32]:
    return (q, 1)  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)


# a @nocopy sibling element: the second unpack sees the first one's write
def with_nocopy(t: tuple[Own[P] | None, Own[Q]]) -> None:
    a, b = t
    if a is not None:
        a.bump()
    c, d = t
    if c is not None:
        print("with_nocopy", c.x, d.x)


# a nested def called in the body reads the non-consuming target in place
def nonescaping(t: tuple[Own[P] | None, int32]) -> int32:
    a, n = t
    c, m = t

    def g() -> int32:
        if a is not None:
            return a.x + n
        return n
    if c is not None:
        c.bump()
    return g()


# an escaping closure over a non-consuming target is refused
# (error_own_optional_target_escaping_capture); a narrowed copy is the remedy
def escaping_copy(t: tuple[Own[P] | None, int32]) -> Callable[[], int32]:
    a, n = t
    c, m = t
    if a is not None:
        a2 = copy(a)

        # a2 is not used after g: the "used after" warning is false
        # (BUGS.md#branch-closure-false-used-after-copy)
        def g() -> int32:  # tpyc: warning(/copies local 'a2' \(used after closure definition/)
            return a2.x + n
        if c is not None:
            c.bump()
        return g
    return lambda: n


# a lambda parameter shadowing a rebound non-consuming target is a scope of
# its own: the inner lambda captures the parameter, not the target, and is
# not refused
def shadowed(t: tuple[Own[P] | None, int32]) -> int32:
    a, n = t
    c, m = t
    a = P(9)
    mk: Callable[[P], Callable[[], int32]] = lambda a: lambda: a.x  # tpyc: warning(/copies the object 'a'/)
    q = P(4)
    return mk(q)() + a.x + n


# a Send-marked owned element over an open T (the bare spelling is not
# owned: BUGS.md#send-own-tuple-element-not-owned)
def marked[T](t: tuple[Send[Own[T]] | None, int32]) -> None:
    pass


def main() -> None:
    p = P(1)
    print("consume", consume((p, 1)))
    rb = borrow((P(5), 2))
    print("borrow", rb)
    r = ret_after((P(5), 2))
    if r is not None:
        print("ret_after", r.x)
    print("in_loop", in_loop((P(5), 1)))
    h = H((P(3), 1))
    h.put((P(4), 1))
    if h.p is not None:
        h.p.bump()
        print("field", h.p.x)
    print("in_match", in_match((P(6), 1), 1))
    q = P(2)
    print("mixed", mixed((P(1), q)), q.x)
    r2 = two_owned((P(7), P(8)))
    print("two_owned", r2)
    rebind_fresh((P(5), 1))
    print("rebind_consumed_fresh", rebind_consumed_fresh((P(5), 1)))
    print("rebind_consumed_none", rebind_consumed_none((P(5), 1)))
    print("dropped", dropped((P(1), P(2))))
    q5 = P(2)
    print("forward_mixed", forward_mixed((P(1), q5)), q5.x)
    q6 = P(2)
    print("forward_mixed_again", forward_mixed_again((P(1), q6)), q6.x)
    rebind_none((P(5), 1))
    print("ro", ro_tuple((P(3), 1)), ro_payload((P(4), 2)))
    print("forward", forward_then_unpack((P(1), 1)))
    a, n = make(3)
    if a is not None:
        print("make", a.x, n)
    b, m = (P(4), 2)
    print("literal", b.x, m)
    c, j = make_named(5)
    if c is not None:
        print("make_named", c.x, j)
    f1, k1 = make_finally(3)
    if f1 is not None:
        print("make_finally", f1.x, k1)
    q4 = P(6)
    e, i = make_from(q4)
    if e is not None:
        print("make_from", e.x, i, q4.x)
    with_nocopy((P(7), Q(8)))
    print("nonescaping", nonescaping((P(5), 2)))
    print("shadowed", shadowed((P(5), 2)))
    print("escaping_copy", escaping_copy((P(5), 2))())
    marked((P(7), 1))
    print("marked")
    # calls: last use, fresh, explicit copy
    p1 = P(1)
    print("call last", consume((p1, 1)))
    print("call fresh", consume((P(2), 1)))
    k = P(3)
    print("call copy", consume((copy(k), 1)), k.x)
    print("call none", consume((None, 5)))
    # Optional only: a maybe-None `P | None` local passed at its last use
    for flag in [True, False]:
        o: P | None = None
        if flag:
            o = P(10)
        print("call maybe none", consume((o, 1)))
    # a borrowed argument still read after the call: the warned copy
    keep = P(7)
    print("call borrowed", sink((keep, 1)))  # tpyc: warning(/copies P into owned storage \(argument 't' tuple element 0\)/)
    print("call borrowed", keep.x)


main()
