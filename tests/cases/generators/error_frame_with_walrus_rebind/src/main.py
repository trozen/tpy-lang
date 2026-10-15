# A walrus rebinding a generator's with-as target over an owned `__enter__`
# result is refused, like every walrus rebind of a non-value local
# (BUGS.md#walrus-rebind-nonvalue-local-rejects).
from typing import Iterator

from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Ctx:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> Own[P]:
        return P(1)

    def __exit__(self, kind, value, tb) -> None:
        pass


def g() -> Iterator[int32]:
    with Ctx() as p:
        q = p
        yield 0
        if (p := P(2)).n > 0:  # tpyc: error(/walrus reassignment of non-value local 'p'/)
            q.n += 10
        print(q.n, p.n)
        yield 1


for _ in g():
    pass
