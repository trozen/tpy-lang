# ArrayList.extend() warns when copying non-value elements from an Iterable source.
# copy() suppression is tested in warn_extend_copy (list); here we check ArrayList-specific paths.
from tpy import Int32
from tplib import ArrayList


class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val


def test_extend_warns() -> None:
    a = ArrayList[Node, 16]()
    b: list[Node] = [Node(1)]
    a.extend(b)  # tpyc: warning(/copies Node elements/)
    print(a[0].val)
    print(len(b))

def test_extend_value_type_no_warn() -> None:
    a = ArrayList[Int32, 16]()
    b: list[Int32] = [1, 2]
    a.extend(b)  # tpyc: ok
    print(a[0])

def test_ctor_warns() -> None:
    b: list[Node] = [Node(1)]
    a = ArrayList[Node, 16](b)  # tpyc: warning(/copies Node elements/)
    print(a[0].val)
    print(len(b))

def test_ctor_last_use_no_warn() -> None:
    b: list[Node] = [Node(1)]
    a = ArrayList[Node, 16](b)  # tpyc: ok -- b's last use
    print(a[0].val)

test_extend_warns()
test_extend_value_type_no_warn()
test_ctor_warns()
test_ctor_last_use_no_warn()
