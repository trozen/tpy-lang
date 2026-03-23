# For-loop container-level move: when the source list is at its last use,
# the vector is moved into OwnIter so the source variable is freed early.
# Note: individual elements are not yet moved into the loop variable
# (const auto& binding); that requires a loop variable binding fix.
from tpy import Int32

class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

def test_list_container_move_last_use() -> None:
    src: list[Node] = [Node(1), Node(2), Node(3)]
    total: Int32 = 0
    for x in src:
        total += x.val
    print(total)

def test_list_borrowing_not_last_use() -> None:
    src: list[Node] = [Node(10), Node(20)]
    total: Int32 = 0
    for x in src:
        total += x.val
    print(total)
    print(len(src))

def test_value_type_no_consuming() -> None:
    src: list[Int32] = [1, 2, 3]
    total: Int32 = 0
    for x in src:
        total += x
    print(total)

test_list_container_move_last_use()
test_list_borrowing_not_last_use()
test_value_type_no_consuming()
