# A tuple local bound from a borrowing call holds its @nocopy elements by
# reference, so storing it as a container element at its last use would copy
# them: a clean error, like the scalar, rather than a failed C++ build. A
# documented divergence: CPython runs this (it aliases), while a @nocopy value
# "can only be moved, never copied" (LANGUAGE_FEATURES "@nocopy Types").
from tpy import int32, Own, nocopy


@nocopy
class B:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk(b: B) -> tuple[B, B]:
    return (b, b)


def store(b: B) -> Own[list[tuple[B, B]]]:
    t = mk(b)
    return [t]  # tpyc: error(/cannot copy non-copyable type 'B' into owned storage \(tuple element 0\)/)


def main() -> None:
    b = B(1)
    print(len(store(b)))


main()
