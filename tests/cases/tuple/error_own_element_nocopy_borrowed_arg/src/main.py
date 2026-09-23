# A borrowed @nocopy member at a tuple element's Own[T] argument slot is still
# refused: the copy the warning would declare cannot be built.
from tpy import int32, Own, nocopy


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def take(t: Own[tuple[Tok, int32]]) -> int32:
    a, k = t
    return a.n + k


def f(tok: Tok) -> int32:
    # The borrowed parameter cannot be copied into the owned element.
    return take((tok, 1))  # tpyc: error(/Tok.*cannot be passed as argument 't' tuple element 0/)


def main() -> None:
    print(f(Tok(1)))


main()
