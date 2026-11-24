# A literal at a parameter typed as a union with a reference member lands
# in its scalar member, which the pointer variant cannot point at, so the
# argument is refused (BUGS.md#ptr-union-literal-arg-as-const).
from tpy import int32


class Node:
    def __init__(self, n: int32) -> None:
        self.n = n


def show(u: Node | int32) -> None:
    print(isinstance(u, Node))


def main() -> None:
    show(3)  # tpyc: error(/arg.borrow_source/)


main()
