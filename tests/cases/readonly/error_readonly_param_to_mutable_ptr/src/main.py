# A `readonly[T]` parameter cannot be coerced to a mutable `Ptr[T]`
# (or to a mutable `Ptr[Parent]` / `Ptr[@dynamic Protocol]` via the
# upcast variants) -- the upcast requires a mutable lvalue, and a
# readonly binding doesn't qualify.
from tpy import Int32, Ptr, readonly


class Node:
    x: Int32
    def __init__(self) -> None:
        self.x = 0


def take_mut(p: Ptr[Node]) -> None:
    pass


def caller(n: readonly[Node]) -> None:
    take_mut(n)  # tpyc: error(/Cannot take mutable pointer to read-only or temporary value/)


def main() -> None:
    n = Node()
    caller(n)


main()
