# List comprehension over @nocopy elements: the body calls a non-readonly
# method (Rc.clone()) on the loop var. Verifies that sema drops const_loop_var
# when it detects mutation, and that the trailing __result is moved out of the
# comprehension's stmt-expr block (otherwise the deleted Rc copy ctor fires
# at the consumer).
from tpy import Int32
from tplib import Rc


class Node:
    value: Int32

    def __init__(self, v: Int32) -> None:
        self.value = v


def main() -> None:
    originals: list[Rc[Node]] = [
        Rc.new(Node(10)),
        Rc.new(Node(20)),
        Rc.new(Node(30)),
    ]
    clones: list[Rc[Node]] = [x.clone() for x in originals]
    for c in clones:
        print(c.get().value)


main()
