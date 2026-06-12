# Multi-target assignment evaluates the value once, then assigns
# targets left-to-right (CPython order); aliasing is preserved.
from tpy import Int32


def main() -> None:
    a: list[Int32] = [0, 0]
    b = 0
    a[(b := 1)] = b = 7
    print(a, b)
    c = d = [1, 2]
    d.append(3)
    print(c)


main()
