# A `*args` pack of temporaries lends each element: returning its element by
# reference would dangle (LANGUAGE_FEATURES "Owned Return Values").
from tpy import readonly


class Node:
    def __init__(self, v: int) -> None:
        self.v = v


def first(*xs: readonly[Node]) -> readonly[Node]:
    return xs[0]


def from_temps() -> readonly[Node]:
    return first(Node(1), Node(2))  # tpyc: error(/Cannot return local or temporary/)


def main() -> None:
    print(from_temps().v)


main()
