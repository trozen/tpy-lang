# A comprehension element mixing a FRESH ctor with a BORROWED name member keeps
# the element out of the routed family.
from tpy import int32


class Payload:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def build(b: Payload) -> int32:
    xs = [(Node(i), b) for i in range(2)]  # tpyc: error(/expr.list_comp/)
    return len(xs)


def main() -> None:
    print(build(Payload(1)))


main()
