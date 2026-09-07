# A list literal assigned to an `Any`-typed field (a bare `None` source does
# compile): TPy does not lower this shape yet, so the case pins the reject.
from typing import Any
from tpy import Int32


class Node:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Holder:
    payload: Any

    def __init__(self) -> None:
        self.payload = None


def store(h: Holder) -> None:
    h.payload = [1, 2]  # tpyc: error(/assign.field_write_shape/)


def main() -> None:
    h = Holder()
    store(h)
    print(h.payload is None)


main()
