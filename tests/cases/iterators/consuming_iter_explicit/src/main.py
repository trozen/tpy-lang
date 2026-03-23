# Explicit b.__iter__() selects consuming overload when b is at last use.
# Consuming: own_iter(std::move(b)), borrowing: __iter__(b).
from tpy import Int32

class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

def test_consuming_last_use() -> None:
    b: list[Node] = [Node(1), Node(2)]
    it = b.__iter__()
    total: Int32 = 0
    for x in it:
        total += x.val
    print(total)

def test_borrowing_not_last_use() -> None:
    b: list[Node] = [Node(10), Node(20)]
    it = b.__iter__()
    total: Int32 = 0
    for x in it:
        total += x.val
    print(total)
    print(len(b))

test_consuming_last_use()
test_borrowing_not_last_use()
