# An Own[T] param is callee-owned and dies at function exit: returning it
# into a borrow-form pointer-repr Optional return must be rejected.
from tpy import int32, Own


class P:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


def ret_optional(x: Own[P | None]) -> P | None:
    return x  # tpyc: error(/would dangle/)


def main() -> None:
    pass


main()
