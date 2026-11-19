# One body calling a generic both inferred and spelled at its own `T`: the
# inferred call takes `T`'s storage form (`store2<::tpy::storage_t<T>>`), the
# spelled one `T` itself (`store2<T>`); the two instantiations stay distinct.
from typing import Iterator
from tpy import Own, int32


class Q:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def store1[T](x: T, out: list[T]) -> None:
    out.append(x)  # tpyc: warning(/may copy T into owned storage/)


def gen[T](x: T) -> Iterator[T]:
    yield x


def both[T](x: T, out: list[T]) -> None:
    store1(x, out)  # tpyc: ok
    store1[T](x, out)  # tpyc: ok
    for y in gen(x):
        out.append(y)  # tpyc: warning(/may copy T into owned storage/)
    for y in gen[T](x):
        out.append(y)  # tpyc: warning(/may copy T into owned storage/)


def main() -> None:
    out: list[Q] = []
    both(Q(1), out)
    # the first spelled hand-off of `x` copies at `T = Own[Q]` (warned here)
    both[Own[Q]](Q(2), out)  # tpyc: warning(/copies Q into owned storage/)
    print("unowned", len(out), out[0].n, out[4].n)
