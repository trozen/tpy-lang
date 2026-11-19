# An open result held in a local: the accumulator holds each result as the
# twin's local does -- a value snapshot (`int32_t` / `std::string`) at a value
# `T`, the element itself at a reference `T`.
from tpy import Fn, int32
from fixtures import P, add, bigger


def red[T](func: Fn[[T, T], T], xs: list[T]) -> T:
    acc = xs[0]
    for i in range(1, len(xs)):
        acc = func(acc, xs[i])  # tpyc: ok
    return acc


def red_touch(func: Fn[[P, P], P], xs: list[P]) -> P:
    return red(func, xs)


def red_snapshot[T](func: Fn[[T, T], T], xs: list[T], v: T) -> T:
    acc = xs[0]
    # a later write to the list slot: a value `T` local keeps its snapshot
    xs[0] = v
    for i in range(1, len(xs)):
        acc = func(acc, xs[i])
    return acc


def concat(a: str, b: str) -> str:
    return a + b


def fin[T](x: T) -> T:
    try:
        # a bare `x: T` returned under a finally: lent at `T = P`
        return x
    finally:
        print("red-fin finally")


def main() -> None:
    # value T: the accumulator holds each result
    print("red-int", red(add, [1, 2, 3]))
    print("red-str", red(concat, ["a", "b", "c"]))
    # reference T: the result is the list's element itself
    ps = [P(1), P(5), P(3)]
    red_touch(bigger, ps).n += 100
    print("red-ref", ps[1].n)
    # a write to the source after the binding: an int is a snapshot
    print("red-snap-int", red_snapshot(add, [1, 2], 10))
    x = P(4)
    fin(x).n += 1
    print("red-fin", x.n)
