# A hidden name at each position: method receiver, ctor, generator, async,
# with, try/finally, match guard and arm, @error_return, lambda in a comp.
import asyncio
from typing import Callable, Iterator
from tpy import Array, Fn, int32, error_return, ReturnException


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def tot(self, ps: list["P"]) -> int32:
        # The variable hides the receiver: the element's x, then the outer.
        s = [self.x for self in ps]  # tpyc: ok
        self.x += 1
        return self.x + s[0] + s[1]

    def gen(self, ps: list["P"]) -> Iterator[int32]:
        # A generator method's `self` hidden.
        r = [self.x for self in ps]  # tpyc: ok
        self.x += 1
        yield r[0] + self.x
        yield r[1] + self.x

    def arr_self(self, arr: Array["P", 2]) -> int32:
        # The receiver hidden in an Array comprehension over an Array source.
        a = [self.x for self in arr]  # tpyc: ok
        self.x += 1
        return a[0] + a[1] + self.x

    def bump(self) -> int32:
        self.x += 1
        return self.x

    def bump_all(self, ps: list["P"]) -> int32:
        # A write through the hidden receiver writes the element: `ps` is
        # written, as the renamed twin's.
        r = [self.bump() for self in ps]  # tpyc: ok
        return r[0] + self.x

    def pop_all(self, rows: list[list[int32]]) -> int32:
        r = [self.pop() for self in rows]  # tpyc: ok
        return r[0] + self.x

    def lam_self(self, ps: list["P"]) -> int32:
        # A lambda reading the hidden `self` captures the element. Both
        # warnings are right: the `Callable` closure copies the element (a
        # later mutation of it is invisible, observed by
        # nested_def/lambda_capture_copy_warn `comp_self`), and CPython's
        # closures share the one late-bound variable.
        fs: list[Callable[[], int32]] = [lambda: self.x for self in ps]  # tpyc: warning(/copies the object 'self'/) warning(/bound again on each iteration/)
        # The last closure reads the last element under either binding rule.
        return fs[-1]() + self.x

    def lam_param_self(self, other: "P") -> int32:
        # A lambda parameter named `self` is no write through the receiver.
        f: Callable[["P"], int32] = lambda self: self.bump()  # tpyc: ok
        return f(other) + self.x

    def lam_alias_recv(self) -> int32:
        # A write through an alias of the receiver inside a lambda whose
        # parameter is `self` still writes the receiver: the method is not
        # const.
        me = self
        return call1(lambda self: me.bump() + self, 10)  # tpyc: ok


class Bucket:
    rows: list[list[int32]]

    def __init__(self) -> None:
        self.rows = [[1, 2], [3, 4]]

    def drain(self) -> int32:
        # The variable hides the receiver, and the source is the receiver's
        # field, read outside the clause: each row is popped.
        vs = [self.pop() for self in self.rows]  # tpyc: ok
        return vs[0]

    def twin(self) -> int32:
        # Neighbour: the renamed twin.
        vs = [row.pop() for row in self.rows]
        return vs[0]


class C:
    xs: list[int32]

    def __init__(self, xs: list[int32]) -> None:
        self.xs = [xs * 2 for xs in xs]  # tpyc: ok
        xs.append(0)


class Ctx:
    def __enter__(self) -> int32:
        return 4

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def gen_body(v: int32, xs: list[int32]) -> Iterator[int32]:
    yield sum([v + 1 for v in xs])  # tpyc: ok
    yield v


async def async_body(v: int32, xs: list[int32]) -> int32:
    await asyncio.sleep(0)
    return sum([v * 3 for v in xs]) + v  # tpyc: ok


async def work() -> int32:
    return 42


async def unread(xs: list[int32]) -> int32:
    # The variable is no read of the outer coroutine, still never consumed.
    c = work()  # tpyc: warning(/never consumed/)
    return sum([c + 1 for c in xs])  # tpyc: ok


async def consumed_coro(xs: list[int32]) -> int32:
    # The variable's read does not consume the outer coroutine, awaited after.
    c = work()  # tpyc: ok
    r = sum([c + 1 for c in xs])  # tpyc: ok
    return r + await c


class Hold:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]

    def __enter__(self) -> list[int32]:
        return self.items

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def ptr_self_shadow(h: Hold) -> None:
    with h as xs:
        ys = xs
        # A pointer local hidden by the variable over its own source.
        r = [ys + 1 for ys in ys]  # tpyc: ok
        ys.append(5)
        print("ptr_self_shadow", r, xs)


def stepped(v: int | str, ys: list[int32]) -> None:
    if isinstance(v, str):
        # The element is met again at max's slot: the narrowed v stays hidden.
        print("stepped", max([4 if v > 1 else 5 for v in ys]), v.upper())  # tpyc: ok


def with_try(xs: list[int32]) -> None:
    with Ctx() as v:
        print("with", [v + 1 for v in xs], v)  # tpyc: ok
    try:
        n = 1
        print("try", [n + 2 for n in xs], n)  # tpyc: ok
    finally:
        print("finally", [n for n in xs])  # tpyc: ok


def matched(t: P, xs: list[int32]) -> None:
    match t:
        case P(x=a) if any([a > 5 for a in xs]):  # tpyc: ok
            print("guard", a)
        case P(x=a):
            print("arm", [a * 2 for a in xs], a)  # tpyc: ok


class Neg(Exception, ReturnException):
    pass


@error_return(Neg)
def er(v: int32, xs: list[int32]) -> int32:
    if v < 0:
        raise Neg
    return sum([v for v in xs]) + v  # tpyc: ok


def lambdas(ps: list[P]) -> None:
    a = P(100)
    # A lambda in the comprehension reads the element, not the outer `a`
    # (both warnings are the existing late-binding policy).
    fs: list[Callable[[], int32]] = [lambda: a.x for a in ps]  # tpyc: warning(/copies the object 'a'/) warning(/bound again on each iteration/)
    a.x += 1
    print("lambda", len(fs), a.x)


def free_lam_self(ps: list[P]) -> int32:
    # The free-function face: `self` is only the comprehension's. The copy
    # warning is right (see `lam_self`); the mutation below is of another
    # element, so the output stays CPython's.
    fs: list[Callable[[], int32]] = [lambda: self.x for self in ps]  # tpyc: warning(/copies the object 'self'/) warning(/bound again on each iteration/)
    ps[0].x += 5
    return fs[-1]() + ps[0].x


def call0(f: Fn[[], int32]) -> int32:
    return f()


def call1(f: Fn[[int32], int32], v: int32) -> int32:
    return f(v)


def lam_writes_capture(xs: list[int32], ys: list[int32]) -> None:
    # A lambda in the element capturing the outer `xs` and writing it keeps
    # `xs` a mutable parameter.
    r = [call0(lambda: xs.pop()) + y for y in ys]  # tpyc: ok
    print("lam_writes_capture", r, xs)


def main() -> None:
    print("receiver", P(100).tot([P(1), P(2)]))
    p = P(100)
    ps = [P(1), P(2)]
    print("bump_all", p.bump_all(ps), p.x, ps[0].x)
    print("pop_all", p.pop_all([[1, 2]]), p.x)
    print("lam_self", p.lam_self(ps), free_lam_self(ps))
    print("lam_param_self", p.lam_param_self(ps[1]), p.x, ps[1].x)
    print("lam_alias_recv", p.lam_alias_recv(), p.x)
    bk = Bucket()
    print("recv_field_src", bk.drain(), bk.twin(), bk.rows)
    lam_writes_capture([1, 2, 3], [10, 20])
    print("gen_self", list(P(5).gen([P(1), P(2)])))
    arr: Array[P, 2] = [P(1), P(2)]
    print("arr_self", P(100).arr_self(arr))
    print("unread", asyncio.run(unread([1, 2])))
    print("consumed_coro", asyncio.run(consumed_coro([1, 2])))
    ptr_self_shadow(Hold())
    stepped("a", [1, 2])
    c = C([1, 2])
    print("ctor", c.xs)
    print("generator", list(gen_body(10, [1, 2])))
    print("async", asyncio.run(async_body(10, [1, 2])))
    with_try([5, 6])
    matched(P(1), [7])
    matched(P(1), [3])
    try:
        print("error_return", er(10, [1, 2]))
    except Neg:
        print("error_return neg")
    lambdas([P(1), P(2)])
