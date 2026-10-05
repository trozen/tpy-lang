# MIR pins for owned record results: a record returned by value (`Own[R]`) on
# the definition side (a fresh construct, a fixed local, a copy, a branch per
# return, a forwarded call) and its callers (an owned local, a reseat, a
# handed-over element, a temporary receiver); kept refusals beside them.
from tpy import int32, Own, copy, readonly, nomove, ValueType


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:  # tpyc: mir(covered)
        self.x = x
        self.y = y

    def bump(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.x += 1

    @readonly
    def total(self) -> int32:  # tpyc: mir(covered) mir_summary(known)
        return self.x + self.y


class Named:
    name: str
    n: int32

    def __init__(self, name: str, n: int32) -> None:  # tpyc: mir(covered)
        self.name = name
        self.n = n


class Pool:
    ps: list[Point]

    def __init__(self) -> None:  # tpyc: mir(covered)
        self.ps = []

    # method: a result built from the receiver's fields
    def spawn(self, n: int32) -> Own[Point]:  # tpyc: mir(covered) mir_summary(known)
        return Point(n, len(self.ps))


# free function: a fresh construct returned by value
def make(n: int32) -> Own[Point]:  # tpyc: mir(covered) mir_summary(known)
    return Point(n, n + 1)


# free function: an owned-leaf field copied into the result
def make_named(s: str) -> Own[Named]:  # tpyc: mir(covered) mir_summary(known)
    return Named(s, 1)


# free function: a fixed local returned -- its own storage leaves the body
def make_local(n: int32) -> Own[Point]:  # tpyc: mir(covered) mir_summary(known)
    p = Point(n, 0)  # tpyc: mir_borrowed(p) mir_borrows(p, p)
    p.bump()
    return p


# free function: a copy of a borrowed parameter returned (a copy event, not a move)
def duplicate(p: Point) -> Own[Point]:  # tpyc: mir(covered) mir_summary(known)
    return copy(p)


# free function: a result slot per branch
def pick(flag: bool, n: int32) -> Own[Point]:  # tpyc: mir(covered) mir_summary(known)
    if flag:
        return Point(n, 1)
    return Point(1, n)


# free function: another owned result forwarded
def forward(n: int32) -> Own[Point]:  # tpyc: mir(covered) mir_summary(known)
    return make(n)


# kept refusal: a reassigned local returned (its backing moved after the rebind)
def reassigned(flag: bool, n: int32) -> Own[Point]:  # tpyc: mir(uncovered /^reassigned local returned$/)
    p = Point(n, 0)
    if flag:
        p = Point(0, n)
    return p


# kept refusal: a local reseated by a call, then returned
def reseat_return(n: int32) -> Own[Point]:  # tpyc: mir(uncovered /^reassigned local returned$/)
    p = make(n)
    p = make(n + 1)
    return p


# free function: an owned local initialized by the call, borrowed afterwards
def use_result(n: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    p = make(n)  # tpyc: mir_borrowed(p) mir_borrows(p, p)
    p.bump()
    return p.total()


# free function: the result as a temporary receiver
def use_temp(n: int32) -> int32:  # tpyc: mir(certified) mir_summary(known)
    return make(n).x


# free function: a method call's result as a temporary receiver
def spawn_temp(pool: Pool) -> int32:  # tpyc: mir(certified)
    return pool.spawn(4).y


# free function: a reseat of a local by a second call
def reseat(n: int32) -> int32:  # tpyc: mir(covered) mir_summary(known)
    p = make(n)
    p = make(n + 1)  # tpyc: mir_owned(p) mir_write(p)
    return p.x


# free function: a result handed over to a container
def collect(n: int32) -> int32:  # tpyc: mir(covered)
    ps: list[Point] = []
    ps.append(make(n))
    return ps[0].y


# free function: an owned-leaf field of the result read
def named_caller(s: str) -> int32:  # tpyc: mir(covered)
    m = make_named(s)
    return m.n + len(m.name)


# free function: a method call's result bound to an owned local
def spawned(pool: Pool) -> int32:  # tpyc: mir(covered)
    p = pool.spawn(4)
    return p.x + p.y


# kept refusal: a user parameter taking the record by value. The parameter
# only pins the refusal of an owning record parameter body, so sema's
# never-consumed warning is expected.
def take(p: Own[Point]) -> int32:  # tpyc: warning(/never consumed/) mir(uncovered /^unsupported parameter type$/)
    return p.x


def give(n: int32) -> int32:  # tpyc: mir(uncovered /^call needs finalized known summary$/)
    return take(make(n))


def read(p: Point) -> int32:  # tpyc: mir(covered) mir_summary(known)
    return p.y


# free function: the result lent as a borrowed argument temporary
def lend_temp(n: int32) -> int32:  # tpyc: mir(certified) mir_summary(known)
    return read(make(n))


# kept refusal: an Optional local bound to the result
def maybe(flag: bool, n: int32) -> int32:  # tpyc: mir(uncovered /^unsupported record initializer$/)
    p: Point | None = None
    if flag:
        p = make(n)
    if p is not None:
        return p.x
    return 0


@nomove
class Pinned:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


# kept refusal: a non-movable record returned by value
def make_pinned(n: int32) -> Own[Pinned]:  # tpyc: mir(uncovered /^owned record result needs movable record$/)
    return Pinned(n)


class Tok(ValueType):
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


# kept refusal: a value-type record returned by value
def make_tok(n: int32) -> Own[Tok]:  # tpyc: mir(uncovered /^missing constructor definition$/)
    return Tok(n)


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)


# kept refusal: a generic record returned by value
def make_box(n: int32) -> Own[Box[int32]]:  # tpyc: mir(uncovered /^unsupported return type$/)
    return Box(n)


class Holder:
    p: Point

    def __init__(self) -> None:
        self.p = Point(0, 0)

    # kept refusal: a result stored into a record field
    def put(self, n: int32) -> None:  # tpyc: mir(uncovered /^record field replacement is unsupported$/)
        self.p = make(n)


def main() -> None:
    print("make", make(1).y)
    print("make_named", make_named("ab").n)
    print("make_local", make_local(4).x)
    a = Point(1, 2)
    q = duplicate(a)
    a.bump()
    print("duplicate", q.total(), a.x)
    print("pick", pick(True, 7).x, pick(False, 7).x)
    print("forward", forward(2).y)
    print("reassigned", reassigned(False, 3).x, reassigned(True, 3).x)
    print("reseat_return", reseat_return(3).x)
    print("use_result", use_result(3))
    print("use_temp", use_temp(4))
    print("spawn_temp", spawn_temp(Pool()))
    print("reseat", reseat(5))
    print("collect", collect(6))
    print("named_caller", named_caller("ab"))
    print("spawned", spawned(Pool()))
    print("give", give(1))
    print("lend_temp", lend_temp(2))
    print("maybe", maybe(True, 9), maybe(False, 9))
    print("make_pinned", make_pinned(5).x)
    print("make_tok", make_tok(6).a)
    print("make_box", make_box(7).v)
    h = Holder()
    h.put(8)
    print("put", h.p.x)


main()
