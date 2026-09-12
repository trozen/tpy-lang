# A non-generic recursive union (type Expr = int32 | list[Expr]) may be a
# structural protocol method parameter.
from tpy import int32
from typing import Protocol

type Expr = int32 | list[Expr]


def depth(e: Expr) -> int32:
    match e:
        case list() as items:
            best = 0
            for c in items:
                d = depth(c)
                if d > best:
                    best = d
            return best + 1
        case _:
            return 0


class Sink(Protocol):
    def take(self, e: Expr) -> int32: ...


class Counter:
    def take(self, e: Expr) -> int32:
        return depth(e)


def run(s: Sink, e: Expr) -> int32:
    return s.take(e)


def main() -> None:
    e: Expr = [1, [2, 3]]
    print(run(Counter(), e))


main()
