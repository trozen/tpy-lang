# A @nocopy name a nested def captures cannot be returned into an owning
# slot: a captured name is never moved (the closure may still run after the
# value is built -- here from a `__del__`), and a copy is not allowed.
from typing import Callable
from tpy import int32, Own, nocopy


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    f: Callable[[], int32]

    def __init__(self, f: Callable[[], int32]) -> None:
        self.f = f

    def __del__(self) -> None:
        print(self.f())


def f(ys: Own[Tok]) -> Own[Tok]:
    def peek() -> int32:
        return ys.n

    h = Holder(lambda: peek())
    return ys  # tpyc: error(/@nocopy.*used after this point/)


def main() -> None:
    f(Tok(1))


main()
