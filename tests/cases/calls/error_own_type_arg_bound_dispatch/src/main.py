# A @dispatch set whose every variant's bound refuses the type `T = Own[P]`
# owns is refused, as the set is at `T = P` (the same message).
from tpy import Fn, Hashable, Own, ValueType, dispatch, int32, int64


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make(k: int32) -> Own[P]:
    return P(k)


@dispatch
def keyed[A, T: ValueType](mk: Fn[[A], T], k: A, n: int32) -> int32:
    return 1


@dispatch
def keyed[A, T: Hashable](mk: Fn[[A], T], k: A, n: int64) -> int32:
    return 2


def main() -> None:
    n: int32 = 0
    print(keyed(make, 1, n))  # tpyc: error(/No matching overload for 'keyed'/)


main()
