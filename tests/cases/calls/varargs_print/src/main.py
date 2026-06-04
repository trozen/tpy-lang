# A whole *args body view prints tuple-style, matching CPython: (a, b, c),
# (a,) for a single element (singleton trailing comma), () for empty.
# (Regression guard: it must reach the printer, not a C++ operator<< error.)
def show(*xs: int) -> None:
    print(xs)


def main() -> None:
    show(1, 2, 3)
    show(7)
    show()


main()
