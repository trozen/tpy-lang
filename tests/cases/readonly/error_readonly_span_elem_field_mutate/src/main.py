# Writing a field through a Span[readonly[Box]] element (reached by subscript)
# is rejected at sema, not left to a C++ const error.
from tpy import Span, readonly


class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v


def f(xs: Span[readonly[Box]]) -> None:
    xs[0].val = 9  # tpyc: error(/Cannot mutate readonly reference/)


def main() -> None:
    pass


main()
