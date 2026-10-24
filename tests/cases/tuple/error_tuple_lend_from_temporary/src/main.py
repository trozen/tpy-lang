# A tuple element lent out of a temporary -- here the member of a call's
# result handed to a borrow-form parameter -- is refused, as master's
# C++ build refused it (BUGS.md#tuple-lend-from-temporary-refused).
from tpy import Own, int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    t: tuple[int32, A]

    def __init__(self, n: int32) -> None:
        self.t = (n, A(n + 1))


def make(n: int32) -> Own[H]:
    return H(n)


def read(t: tuple[int32, A]) -> int32:
    return t[0] + t[1].v


def main() -> None:
    # the lend out of the temporary `make(1).t` is the refused shape
    print(read(make(1).t))  # tpyc: error(/tuple element borrowed from a temporary value/)


main()
