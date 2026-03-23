# copy_iter() suppresses element-copy warnings on bulk operations.
# Parallel to copy() for single elements, but intended for iterables.
from tpy import Int32, copy, copy_iter


class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val


def test_extend_copy_iter_no_warn() -> None:
    a: list[Node] = []
    b: list[Node] = [Node(1), Node(2)]
    a.extend(copy_iter(b))  # tpyc: ok
    print(len(a))
    print(len(b))

def test_extend_copy_iter_value_type() -> None:
    a: list[Int32] = []
    b: list[Int32] = [1, 2, 3]
    a.extend(copy_iter(b))  # tpyc: ok
    print(len(a))

def test_iadd_copy_iter_no_warn() -> None:
    a: list[Node] = []
    b: list[Node] = [Node(3)]
    a += copy_iter(b)  # tpyc: ok
    print(len(a))
    print(len(b))

def test_list_ctor_copy_iter_no_warn() -> None:
    b: list[Node] = [Node(4)]
    a = list(copy_iter(b))  # tpyc: ok
    print(len(a))
    print(len(b))

def test_extend_no_copy_iter_warns() -> None:
    a: list[Node] = []
    b: list[Node] = [Node(5)]
    a.extend(b)  # tpyc: warning(/copies Node elements/)
    print(len(b))

test_extend_copy_iter_no_warn()
test_extend_copy_iter_value_type()
test_iadd_copy_iter_no_warn()
test_list_ctor_copy_iter_no_warn()
test_extend_no_copy_iter_warns()
