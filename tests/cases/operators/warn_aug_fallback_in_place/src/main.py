# A fallback `+=` (`__add__`, no `__iadd__`) on an owned local updates the
# object in place where Python rebinds: warned (BUGS.md#borrow-unpack-target-rebind-writes-through).
import asyncio
from typing import Iterator
from tpy import int32, Own, nocopy, error_return, ReturnException


class V:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[V]":
        return V(self.n + k)

    def __sub__(self, k: int32) -> "Own[V]":
        return V(self.n - k)


@nocopy
class W:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[W]":
        return W(self.n + k)


class G[T]:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> "Own[G[T]]":
        return G[T](self.n + k)


class E(Exception, ReturnException):
    pass


class Cm:
    on: bool

    def __init__(self) -> None:
        self.on = False

    def __enter__(self) -> "Cm":
        self.on = True
        return self

    def __exit__(self, et, ev, tb) -> None:
        self.on = False


def free_fn() -> None:
    a = V(1)
    b = a
    # free function
    a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
    print("free", a.n, b.n, a is b)


class C:
    def __init__(self) -> None:
        a = V(1)
        b = a
        # constructor local
        a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
        print("ctor", a.n, b.n, a is b)

    def run(self) -> None:
        a = V(1)
        b = a
        # method
        a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
        print("method", a.n, b.n, a is b)


def gen() -> Iterator[int32]:
    a = V(1)
    b = a
    # generator
    a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
    yield a.n
    print("gen", a.n, b.n, a is b)


async def work() -> None:
    a = V(1)
    b = a
    await asyncio.sleep(0)
    # async
    a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
    print("async", a.n, b.n, a is b)


def try_finally() -> None:
    a = V(1)
    b = a
    try:
        print("tryfin try", a.n)
    finally:
        # try/finally
        a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
    print("tryfin", a.n, b.n, a is b)


def with_body() -> None:
    a = V(1)
    b = a
    with Cm() as cm:
        # with body
        a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
        print("with", a.n, b.n, a is b, cm.on)


def match_arm(sel: int32) -> None:
    match sel:
        case 1:
            a = V(1)
            b = a
            # match arm
            a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
            print("match", a.n, b.n, a is b)
        case _:
            print("match other")


@error_return(E)
def err_ret(sel: int32) -> int32:
    a = V(1)
    b = a
    if sel < 0:
        raise E
    # @error_return body
    a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
    print("errret", a.n, b.n, a is b)
    return a.n


def closure_after() -> None:
    a = V(1)
    b = a
    # closure reading the name after the update
    a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)

    def show() -> int32:
        return a.n

    print("closure", show(), b.n, a is b)


def nonlocal_owned() -> None:
    a = V(1)
    b = a

    def bump() -> None:
        nonlocal a
        # nonlocal on an owned enclosing local: the hint names a fresh local
        a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place.*new name, e.g. 'a2 = a \+ 10'/)

    bump()
    print("nonlocal", a.n, b.n, a is b)


def sub_op() -> None:
    a = V(1)
    b = a
    # `-=` falls back to `__sub__`
    a -= 10  # tpyc: warning(/falls back to '__sub__'.*no '__isub__'.*updated in place/)
    print("sub", a.n, b.n, a is b)


def nocopy_rec() -> None:
    a = W(1)
    b = a
    # @nocopy record
    a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
    print("nocopy", a.n, b.n, a is b)


def generic_rec() -> None:
    a = G[str](1)
    b = a
    # generic record
    a += 10  # tpyc: warning(/falls back to '__add__'.*updated in place/)
    print("generic", a.n, b.n, a is b)


def main() -> None:
    free_fn()
    C().run()
    for n in gen():
        print("gen yield", n)
    asyncio.run(work())
    try_finally()
    with_body()
    match_arm(1)
    try:
        r = err_ret(1)
        print("errret ret", r)
    except E:
        print("errret err")
    closure_after()
    nonlocal_owned()
    sub_op()
    nocopy_rec()
    generic_rec()


main()
