# Warn when slice assignment implicitly copies an lvalue list (use copy() to be explicit).
from tpy import int32

class Node:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v

def test_warn_non_last_use() -> None:
    a: list[Node] = [Node(int32(1)), Node(int32(2)), Node(int32(3))]
    b: list[Node] = [Node(int32(10)), Node(int32(20))]
    a[1:3] = b  # tpyc: warning(/copies Node elements/)
    print(b[0].val)  # b used after -> not last use

def test_no_warn_last_use() -> None:
    a: list[Node] = [Node(int32(1)), Node(int32(2)), Node(int32(3))]
    b: list[Node] = [Node(int32(10)), Node(int32(20))]
    a[1:3] = b  # tpyc: ok  (last use of b -> auto-move)

def test_no_warn_literal() -> None:
    a: list[Node] = [Node(int32(1)), Node(int32(2)), Node(int32(3))]
    a[1:3] = [Node(int32(10)), Node(int32(20))]  # tpyc: ok  (temporary)

def test_no_warn_explicit_copy() -> None:
    a: list[Node] = [Node(int32(1)), Node(int32(2)), Node(int32(3))]
    b: list[Node] = [Node(int32(10)), Node(int32(20))]
    a[1:3] = b.copy()  # tpyc: ok  (explicit copy)
    print(b[0].val)

test_warn_non_last_use()
test_no_warn_last_use()
test_no_warn_literal()
test_no_warn_explicit_copy()
