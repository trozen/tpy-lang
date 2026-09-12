# A field read reached through a CHAIN passed at a recursive-alias parameter
# slot: the bare row takes a member-typed field, not a chained one.
# TPy rejects the call `leaf_count(h.inner.n)` today because of this gap.
from tpy import int32, Own

type Tree[T] = T | list[Tree[T]]


def leaf_count(t: Tree[int32]) -> int32:
    return 1


class Inner:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    inner: Inner

    def __init__(self, inner: Own[Inner]) -> None:
        self.inner = inner


def use(h: Holder) -> None:
    print(leaf_count(h.inner.n))  # tpyc: error(/call\.arg_shape/)


def main() -> None:
    use(Holder(Inner(5)))


main()
