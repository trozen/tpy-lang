# @auto_readonly with Ptr[T] return: const overload returns Ptr[readonly[T]].
# Uses explicit auto_readonly[T] annotation on the element type.
from tpy import int32, Ptr, readonly, auto_readonly, take_ptr

class Node:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v


class NodeHolder:
    _node: Ptr[Node]

    def __init__(self) -> None:
        self._node = Ptr[Node]()

    @auto_readonly
    def get_node(self) -> Ptr[auto_readonly[Node]]:
        return self._node


def read_holder(h: readonly[NodeHolder]) -> None:
    p = h.get_node()  # tpyc: type(Ptr[readonly[Node]])
    print(p.value)


def main() -> None:
    n = Node(int32(7))
    h = NodeHolder()
    h._node = take_ptr(n)

    p = h.get_node()  # tpyc: type(Ptr[Node])
    p.value = int32(99)
    print(h.get_node().value)

    n2 = Node(int32(42))
    h._node = take_ptr(n2)
    read_holder(h)


main()
