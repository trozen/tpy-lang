# A write through a reference element of a tuple held by a readonly container
# is refused by sema, as the write through a readonly container's record
# element is: the tuple is a value type, but readonly reaches the object it
# refers to.
from tpy import int32, readonly


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def bump(items: readonly[list[tuple[int32, Box]]]) -> None:
    items[0][1].n = 99  # tpyc: error(/Cannot mutate readonly reference/)


def main() -> None:
    xs = [(1, Box(0))]
    bump(xs)
    print(xs[0][1].n)


main()
