# A @nocopy value consumed in a `break` arm and read after the loop cannot move
# there (the read would see a moved-from value) and cannot be copied.
from tpy import int32, Own, nocopy


@nocopy
class N:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def eat(n: Own[N]) -> int32:
    return n.v


def main() -> None:
    n = N(1)
    r = 0
    for i in range(3):
        if i == 1:
            r = eat(n)  # tpyc: error(/@nocopy type 'N' is used after this point/)
            break
    print(r + n.v)


main()
