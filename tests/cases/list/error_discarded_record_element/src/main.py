# The adjacent shape that keeps rejecting: a discarded stub result whose type is
# NOT a storage-form container result. A record element popped off a
# list[Record] has no bare-statement render, so statement position alone does
# not admit it.
from tpy import Int32


class Rec:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def main() -> None:
    xs = [Rec(1), Rec(2)]
    xs.pop()  # tpyc: error(/method.ret_type/)
    print(len(xs))


main()
