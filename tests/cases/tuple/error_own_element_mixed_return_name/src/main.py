# A tuple local of borrows returned into a MIXED slot (an owned element beside
# a borrowed one) warns its copy but has no render yet: a located reject.
from tpy import int32, Own


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def f(p: P, q: P) -> tuple[Own[P], P]:
    t = (p, q)
    # Element 0 would have to be copied out of the borrow tuple.
    return t  # tpyc: warning(/tuple element 0\)/) error(/return.own_element_copy_source/)


def main() -> None:
    a = P(1)
    x, y = f(a, a)
    print(x.x, y.x)


main()
