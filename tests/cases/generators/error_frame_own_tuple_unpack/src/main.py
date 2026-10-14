# A generator unpack of an `Own[tuple[...]]` call result into a reference
# target is refused: the frame pointer would aim into the case-local tuple
# holder past the next resume (BUGS.md#frame-own-tuple-unpack-dangles).
from typing import Iterator
from tpy import int32, Own


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk(n: int32) -> Own[tuple[P, int32]]:
    return (P(n), n)


def g() -> Iterator[int32]:
    a, k = mk(1)  # tpyc: error(/not yet supported by C\+\+ code generation/)
    yield a.n
    print(a.n, k)
    yield a.n


def main() -> None:
    print(list(g()))


main()
