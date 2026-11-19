# Error: a @nocopy class at a spelled `T = Own[N]` through an argument site
# that copies there (a hand-off of `x` that is not its last use) is refused at
# the call that instantiates the generic, as the hand-written twin's copy is.
from tpy import Own, int32, nocopy


@nocopy
class N:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def store1[T](x: T, out: list[T]) -> None:
    out.append(x)


def twice_own[T](x: T, out: list[T]) -> None:
    store1[T](x, out)
    store1[T](x, out)


def main() -> None:
    out: list[N] = []
    # The subject: the first `store1[T](x, out)` copies an `N`.
    twice_own[Own[N]](N(1), out)  # tpyc: error(/cannot copy non-copyable type 'N'/)
    print(len(out))


main()
