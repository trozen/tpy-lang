# Captured types the wording hedges on or that are copied for their cost: an
# open generic T, an owned str and an int, a rebound Own parameter, a
# @dynamic protocol local and a stateful Callable.
from typing import Callable, Protocol
from tpy import int32, Own, dynamic
from fixtures import P


# generic: a type holding an open T is not settled here, so the wording hedges
def keep_t[T](xs: list[T]) -> Callable[[], int32]:
    return lambda: len(xs)  # tpyc: warning(/may keep its own copy of 'xs'/)


# cost: an expensive immutable value (an owned str, an int) is copied
def cost(k: int) -> Callable[[], int]:
    s = "v" + str(k)
    return lambda: k + len(s)  # tpyc: warning(/copies 'k' when it is created/) warning(/copies 's' when it is created/)


# rebound_param: an Own parameter the body may rebind; the wording hedges
def rebound_param(p: Own[P], c: bool) -> None:  # tpyc: warning(/never consumed/)
    if c:
        p = P(5)
    f: Callable[[], int32] = lambda: p.n  # tpyc: warning(/may keep its own copy of 'p'/)
    p.n = 9
    print("rebound_param:", f())


# dynamic: a @dynamic protocol local is a pointer to its object, so the
# capture copies the pointer and the hedged warning is spurious
# (BUGS.md#hedged-capture-copy-unrebound-param)
@dynamic
class Shape(Protocol):
    def area(self) -> int32: ...


class Sq:
    def __init__(self, k: int32) -> None:
        self.k = k

    def area(self) -> int32:
        return self.k * self.k


def dyn_local(sq: Sq) -> None:
    s: Shape = sq
    f: Callable[[], int32] = lambda: s.area()  # tpyc: warning(/may keep its own copy of 's'/)
    sq.k = 3
    print("dynamic:", f())


# callable_state: a captured Callable copies the callable object it holds,
# so the calls through the original do not advance the lambda's copy
class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __call__(self, xs: list[int32]) -> int32:
        self.n += len(xs)
        return self.n


def callable_state() -> None:
    f: Callable[[list[int32]], int32] = Counter()
    g: Callable[[list[int32]], int32] = lambda xs: f(xs)  # tpyc: warning(/may keep its own copy of 'f'/)
    print("callable_state:", f([1]), g([1]), f([1]), g([1]))


def main() -> None:
    kx = [1]
    kf = keep_t(kx)
    kx.append(2)
    print("generic:", kf())
    print("cost:", cost(3)())
    rebound_param(P(1), True)
    dyn_local(Sq(2))
    callable_state()
