# Error: under Iterator[T] (borrow ABI) a yield must hand out a reference to a
# value that outlives the frame. Yielding a freshly-constructed temporary would
# dangle (the val_or_ref slot would point at a destroyed object), so it is
# rejected and the user is pointed at Iterator[Own[T]].
from typing import Iterator


class Node:
    val: int

    def __init__(self, v: int):
        self.val = v


def bad() -> Iterator[Node]:
    yield Node(1)  # tpyc: error(/Cannot yield local or temporary as reference.*Iterator\[Own\[Node\]\]/)


def main() -> None:
    for n in bad():
        print(n.val)


main()
