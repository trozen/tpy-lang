# A @nocopy name a nested def captures is never moved in the enclosing body,
# so forwarding it to an owning parameter would need a copy, which a
# @nocopy type does not allow.
from tpy import int32, Own, nocopy


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def take(t: Own[Tok]) -> int32:
    return t.n


def f(ys: Own[Tok]) -> int32:
    def peek() -> int32:
        return ys.n
    r = take(ys)  # tpyc: error(/@nocopy.*used after this point/)
    return r + peek()


def main() -> None:
    print(f(Tok(1)))


main()
