# Printing a nullable borrow tuple: the optional holds element pointers,
# which the tuple printer (spelled over the stored elements) cannot take,
# so it is refused rather than handed to the C++ build. The bare twin
# `print(t)` of a `tuple[Box, Box]` local prints.
from tpy import int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __repr__(self) -> str:
        return f"Box({self.n})"


def both(a: Box, b: Box, k: bool) -> tuple[Box, Box] | None:
    if k:
        return None
    return (a, b)


def main() -> None:
    a = Box(1)
    b = Box(2)
    print(both(a, b, False))  # tpyc: error(/print.arg/)


main()
