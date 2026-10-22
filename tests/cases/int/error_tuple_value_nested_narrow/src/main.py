# A tuple variable nested in a tuple literal is refused at any depth, not
# truncated through the C++ tuple conversion (BUGS.md#tuple-value-element-narrowing).
from tpy import int8, int32


def f(p: tuple[int32, int32]) -> tuple[int32, tuple[int8, int8]]:
    return (1, p)  # tpyc: error(/a tuple element is int32, which int8 does not hold every value of/)


def main() -> None:
    print(f((300, 2)))


main()
