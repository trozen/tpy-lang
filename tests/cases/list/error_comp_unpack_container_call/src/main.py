# Tuple UNPACK in a comprehension over a container-returning call: the storage
# form the unpack would register is unwitnessed, so the shape rejects.
from tpy import Int32, Own


def make() -> Own[list[tuple[Int32, Int32]]]:
    return [(1, 2), (3, 4)]


def sums() -> None:
    print([a + b for a, b in make()])  # tpyc: error(/expr.list_comp/)


def main() -> None:
    sums()


main()
