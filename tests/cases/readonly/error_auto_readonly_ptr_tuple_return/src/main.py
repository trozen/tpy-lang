# Tripwire for BUGS.md#auto-readonly-ptr-tuple-return-rejects: an
# @auto_readonly method returning a tuple with a Ptr[auto_readonly[T]] element
# does not lower yet. When it does, this becomes a section of
# readonly/auto_readonly_components (the per-component contract for tuples).
from tpy import int32, Ptr, auto_readonly


class Node:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v


class H:
    _node: Ptr[Node]

    def __init__(self, n: Node) -> None:
        self._node = n

    @auto_readonly
    def pair(self) -> tuple[Ptr[auto_readonly[Node]], int32]:
        return (self._node, 1)  # tpyc: error(/return\.slot_type/)


def main() -> None:
    n = Node(0)
    h = H(n)
    print(n.value)


main()
