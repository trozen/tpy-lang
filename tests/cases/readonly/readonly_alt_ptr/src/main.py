# @readonly_alt with Ptr[T] return: const overload returns ReadOnlyPtr[T].
# Uses explicit readonly_alt[T] annotation on the element type.
from tpy import Int32, Ptr, ReadOnlyPtr, readonly, readonly_alt

class Node:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v


class NodeHolder:
    _node: Ptr[Node]

    def __init__(self) -> None:
        self._node = Ptr[Node]()

    @readonly_alt
    def get_node(self) -> Ptr[readonly_alt[Node]]:
        return self._node


def read_holder(h: readonly[NodeHolder]) -> None:
    p = h.get_node()  # tpyc: type(ReadOnlyPtr[Node])
    print(p.value)


def main() -> None:
    n = Node(Int32(7))
    h = NodeHolder()
    h._node = Ptr(n)

    p = h.get_node()  # tpyc: type(Ptr[Node])
    p.value = Int32(99)
    print(h.get_node().value)

    n2 = Node(Int32(42))
    h._node = Ptr(n2)
    read_holder(h)


main()
