# A tuple PARAM with a borrowed @nocopy element, returned by name into an
# Own[T] element slot, is refused: the element would have to be copied.
from tpy import int32, Own, nocopy


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(t: tuple[Tok, int32]) -> tuple[Own[Tok], int32]:
    return t  # tpyc: error(/cannot copy non-copyable type 'Tok' into owned storage \(tuple element 0\)/)


def main() -> None:
    a, k = f((Tok(1), 0))
    print(a.n, k)


main()
