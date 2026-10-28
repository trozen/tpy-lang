# A truth test of a tuple Optional element is refused, located
# (BUGS.md#tuple-optional-element-truth-test-rejects): the stored-optional
# route would skip the record's `__bool__`.
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(t: tuple[Box | None, int32]) -> None:
    if t[0]:  # tpyc: error(/cond.subscript/)
        print("yes")


def main() -> None:
    f((Box(1), 1))


main()
