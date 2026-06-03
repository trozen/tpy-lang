# Writing a field through a readonly[Box] *args element (reached by iteration)
# is rejected at sema, not left to a C++ const error.
from tpy import readonly


class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v


def f(*xs: readonly[Box]) -> None:
    for b in xs:
        b.val = 9  # tpyc: error(/Cannot mutate readonly reference/)


def main() -> None:
    f(Box(1), Box(2))


main()
