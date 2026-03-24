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
    ci = copy_iter(b)  # tpyc: type(/CopyIter\[Node\]/)
    a.extend(ci)  # tpyc: ok
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

def test_for_loop_copy_iter() -> None:
    b: list[Node] = [Node(10), Node(20)]
    total: Int32 = 0
    for x in copy_iter(b):
        total += x.val
    print(total)
    print(len(b))

test_extend_copy_iter_no_warn()
test_extend_copy_iter_value_type()
test_iadd_copy_iter_no_warn()
test_list_ctor_copy_iter_no_warn()
test_extend_no_copy_iter_warns()
test_for_loop_copy_iter()
