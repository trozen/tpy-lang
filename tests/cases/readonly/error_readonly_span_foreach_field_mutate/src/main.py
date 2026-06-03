# Writing a field through a Span[readonly[Box]] element reached by a for-loop
# is rejected at sema (the iteration path, distinct from subscript).
from tpy import Span, readonly


class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v


def f(xs: Span[readonly[Box]]) -> None:
    for b in xs:
        b.val = 9  # tpyc: error(/Cannot mutate readonly reference/)


def main() -> None:
    pass


main()
