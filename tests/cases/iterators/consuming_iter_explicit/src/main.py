# Explicit b.__iter__() always uses borrowing overload.
# Consuming dispatch only happens through for-loop auto-detection.
from tpy import int32

class Node:
    val: int32
    def __init__(self, val: int32) -> None:
        self.val = val

def test_last_use() -> None:
    b: list[Node] = [Node(1), Node(2)]
    it = b.__iter__()
    total: int32 = 0
    for x in it:
        total += x.val
    print(total)

def test_borrowing_not_last_use() -> None:
    b: list[Node] = [Node(10), Node(20)]
    it = b.__iter__()
    total: int32 = 0
    for x in it:
        total += x.val
    print(total)
    print(len(b))

test_last_use()
test_borrowing_not_last_use()
