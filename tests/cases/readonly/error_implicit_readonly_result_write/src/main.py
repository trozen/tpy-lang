# An implicitly readonly dunder (like @pure and a frozen record's methods)
# declares the reference it returns readonly: a write through the result of
# `a + b` is rejected, not left to fail in C++.
from tpy import int32


class Acc:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __add__(self, o: "Acc") -> "Acc":
        return self


def main() -> None:
    a = Acc()
    b = Acc()
    c = a + b
    c.n = 5  # tpyc: error(/Cannot mutate readonly reference/)


main()
