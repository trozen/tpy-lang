# A fresh tuple literal whose reference member is a CONST source -- a plain
# reference param, or a comprehension loop var -- stored into an owned list
# element builds and copies the member. Output is read-only: the copy is
# acknowledged by the warning, and printing the alias would diverge from CPython.
from tpy import int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def param_member(c: P) -> None:
    xs: list[tuple[int32, P]] = [(1, c)]  # tpyc: warning(/copies P into owned storage/)
    print(xs[0][1].x)


def comp_member(cells: list[P]) -> None:
    xs: list[tuple[int32, P]] = [(1, c) for c in cells]  # tpyc: warning(/copies P into owned storage/)
    print(len(xs), xs[0][1].x)


def main() -> None:
    param_member(P(5))
    comp_member([P(7)])


main()
