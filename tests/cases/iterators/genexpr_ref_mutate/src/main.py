# A generator expression over a concrete reference type hands out a live borrow
# (zero-copy), like a def-generator's Iterator[T]: consumer mutation through the
# yielded element propagates to the source -- CPython semantics (was a silent
# copy before the declaration-driven yield ABI).
class Node:
    val: int

    def __init__(self, v: int):
        self.val = v


def main() -> None:
    data = [Node(1), Node(2), Node(3)]
    for b in (n for n in data):
        b.val = b.val + 100
    for n in data:
        print(n.val)


main()
