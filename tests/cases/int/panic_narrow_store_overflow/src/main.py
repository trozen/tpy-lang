# A value that does not fit a fixed-width field raises an uncaught
# OverflowError, which stops the program.
from tpy import int8


class Holder:
    f8: int8

    def __init__(self, v: int8) -> None:
        self.f8 = v


def main() -> None:
    h = Holder(int8(100))
    h.f8 += 100  # tpyc: ok
    print(h.f8)


main()
