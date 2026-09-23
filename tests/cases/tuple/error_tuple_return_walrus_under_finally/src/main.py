# A walrus-bound member of a tuple returned under a finally has no deferred
# capture (its slot is renamed), so the return rejects, as the scalar
# `return b` does, instead of copying and losing the finally's mutation
# (BUGS.md#walrus-local-return-under-finally-rejects).
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f() -> tuple[Own[Box], int32]:
    if (b := Box(3)).n > 0:
        try:
            return (b, 1)  # tpyc: error(/not yet supported.*return\.finally_deferred_tuple/)
        finally:
            b.n += 1
    return (Box(0), 0)


def main() -> None:
    r, k = f()
    print(r.n, k)


main()
