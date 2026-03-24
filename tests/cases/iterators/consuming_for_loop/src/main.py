# For-loop consuming iteration with explicit own_iter(): moves the container
# into OwnIter, elements accessible via auto&& (move-ready forwarding ref).
# Without own_iter(), for-loops always use borrowing iteration.
from tpy import Int32, own_iter

class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

def test_own_iter_explicit() -> None:
    src: list[Node] = [Node(1), Node(2), Node(3)]
    oi = own_iter(src)  # tpyc: type(/OwnIter\[Node\]/)
    total: Int32 = 0
    for x in oi:
        total += x.val
    print(total)

def test_own_iter_value_type() -> None:
    src: list[Int32] = [10, 20, 30]
    total: Int32 = 0
    for x in own_iter(src):
        total += x
    print(total)

def test_borrowing_default() -> None:
    src: list[Node] = [Node(10), Node(20)]
    total: Int32 = 0
    for x in src:
        total += x.val
    print(total)
    print(len(src))

def test_value_type_borrowing() -> None:
    src: list[Int32] = [1, 2, 3]
    total: Int32 = 0
    for x in src:
        total += x
    print(total)

test_own_iter_explicit()
test_own_iter_value_type()
test_borrowing_default()
test_value_type_borrowing()
