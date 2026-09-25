# A @nocopy value consumed in a handler and read by the finally cannot move
# there (a later raise would leave the finally a moved-from value) or copy.
from tpy import int32, Own, nocopy


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def eat(n: Own[N]) -> int32:
    return n.v


def boom(c: bool) -> None:
    if c:
        raise ValueError("boom")


def main() -> None:
    n = N(1)
    try:
        boom(True)
    except ValueError:
        eat(n)  # tpyc: error(/@nocopy type 'N' is used after this point/)
        boom(True)
        n = N(2)
    finally:
        print(n.v)


main()
