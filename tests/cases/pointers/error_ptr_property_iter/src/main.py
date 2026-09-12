# A plain container MEMBER of a Ptr binding is an admitted for-each iterable;
# the @property one line over is a method call through the pointer and is not.
from tpy import int32, Own, Ptr


class Node:
    xs: list[int32]

    def __init__(self, xs: Own[list[int32]]) -> None:
        self.xs = xs

    @property
    def items(self) -> list[int32]:
        return self.xs


def total(p: Ptr[Node]) -> int32:
    acc = 0
    for v in p.items:  # tpyc: error(/method\.ptr_template\.native_member/)
        acc += v
    return acc


def main() -> None:
    n = Node([1, 2])
    print(total(n))


main()
