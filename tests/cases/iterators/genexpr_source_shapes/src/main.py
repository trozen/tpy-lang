# Generator-expression source and loop-var shapes: a bare dict (whose begin() is
# the KEY iterator), a moved container-literal source under a tuple-unpack head,
# an F1-record unpack target, and a value-repr Optional[scalar] loop var.
from tpy import Int32


class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x

    def bump(self) -> Int32:
        self.x += 10
        return self.x


def main() -> None:
    d = {1: 2, 5: 6}
    # A dict source yields its keys, like the for-loop and comprehension forms.
    print(sum(k for k in d))  # tpyc: ok
    # A moved container-literal source destructured by the unpack head.
    print(sum(a for a, b in [(1, 2), (3, 4)]))  # tpyc: ok
    print(sum(b for a, b in [(1, 2), (3, 4)]))  # tpyc: ok
    items = [(P(1), 2), (P(3), 4)]
    # An F1-record unpack target BORROWS off the tuple rather than copying it:
    # the genexpr mutates through `p`, and the source elements show it after.
    print(sum(p.bump() for p, n in items))  # tpyc: ok
    print([p.x for p, n in items])  # tpyc: ok
    xs: list[Int32 | None] = [1, None, 3]
    # A value-repr Optional[scalar] loop var binds the typed optional copy and
    # the filter reads it whole.
    print(sum(1 for x in xs if x is not None))  # tpyc: ok
    print(sum(1 for x in xs if x is None))  # tpyc: ok


main()
