# The slot step of an argument an overload probe analyzed: the winner's
# slot decides what the probe's left open, replacing the probe's decision,
# and a child completed at the winner's slot recomputes its carrier.
from typing import Callable
from tpy import dispatch, int8, int32, int64, uint8


@dispatch
def w(x: int64) -> str:
    return f"int64 {x}"


@dispatch
def w(x: str) -> str:
    return "str"


@dispatch
def w8(x: int8) -> str:
    return f"int8 {x}"


@dispatch
def w8(x: int64) -> str:
    return f"int64 {x}"


# int64 first so CPython's first-applicable @dispatch stand-in agrees; TPy
# ranks by conversion cost, not declaration order
@dispatch
def wu(x: int64) -> str:
    return f"int64 {x}"


@dispatch
def wu(x: uint8) -> str:
    return f"uint8 {x}"


def h8(x: int8) -> str:
    return f"int8 {x}"


@dispatch
def fl(xs: list[int64]) -> str:
    return f"list {xs[0] + 3000000000}"


@dispatch
def fl(xs: str) -> str:
    return "str"


@dispatch
def twice(x: float) -> float:
    return 2 * x


@dispatch
def twice(x: str) -> float:
    return 0.0


def t8[T](x: tuple[int8], y: T) -> int8:
    return x[0]


@dispatch
def apply(f: Callable[[int32], int32]) -> int32:
    return f(1)


@dispatch
def apply(f: Callable[[str], int32]) -> int32:
    return 0


def apply_to[T](fn: Callable[[T], T], v: T) -> T:
    return fn(v)


def show[T](xs: list[int32], tag: T) -> int32:
    return xs[0] * 10 + xs[1]


def steps() -> None:
    c = True
    n: int = 120
    # an unhinted tuple the winner's tuple[int8] converts
    print("tuple-narrow:", t8((n,), 1))  # tpyc: ok
    # the loser (int8) is probed first; the literal fits only the winner
    print("loser-first:", w8(300 if c else 1))  # tpyc: ok
    # an int-literal select at a probe ranks as on master: an arm that does
    # not fit uint8 never picks it
    print("select-mixed-sign:", wu(200 if c else -1), wu(-1 if c else 200))  # tpyc: ok
    # two int literals at one declared int8 parameter take its type
    print("select-declared:", h8(1 if c else 2))  # tpyc: ok
    # one list literal: the probe's open record, the winner's list[int64]
    print("two-slots:", fl([1, 2]))  # tpyc: ok
    # a pending local's cells, handed over at the probe, settled at the winner
    xs = [1, 2]
    print("pending-cells:", fl(xs))  # tpyc: ok
    # a comprehension completed at a generic slot meets its element in its own
    # scope: the lambda reads the comprehension variable
    print("comp-lambda:", show([apply_to(lambda x: x + y, 0) for y in [1, 2]], 0))  # tpyc: warning(/bound again on each iteration/)
    # a generator expression argument the probe met: one frame
    print("genexpr:", twice(sum(v * 1.0 for v in [n, 2])))  # tpyc: ok


def carriers() -> None:
    v = 5000000000.4
    # round's width completes at the winner's int64 under each carrier
    print("unary:", w(-round(v)))  # tpyc: ok
    print("binop:", w(round(v) + 1))  # tpyc: ok
    print("walrus:", w((y := round(v))))  # tpyc: ok
    print("walrus-target:", y)


def escapes() -> None:
    k = 10

    def inc(x: int32) -> int32:
        return x + k

    # one nested function at two @dispatch calls' arguments: one escape mark
    print("escape:", apply(inc), apply(inc))  # tpyc: ok


def main() -> None:
    steps()
    carriers()
    escapes()


main()
