# A constructor's keyword-only parameter cannot be filled positionally,
# as a function's cannot (CPython raises TypeError).
from tpy import int32


class P:
    a: int32
    b: int32

    def __init__(self, a: int32, *, b: int32 = 0) -> None:
        self.a = a
        self.b = b


def main() -> None:
    p = P(1, 2)  # tpyc: error(/'P\(\)' takes 1 positional argument\(s\), got 2/)
    print(p.a, p.b)


main()
