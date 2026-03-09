# Tests bounds check elision in while-loops via literal range tracking.
from tpy import Array, Int32

def test_while_basic() -> None:
    """i = 0; while i < len(arr): arr[i] ... i += 1 -- bounds elided."""
    arr: Array[Int32, 5] = [10, 20, 30, 40, 50]
    i: Int32 = 0
    while i < len(arr):
        print(arr[i])  # tpyc: bounds_safe(arr)
        i += 1

def test_while_increment_before_access() -> None:
    """i += 1 before arr[i] -- increment invalidates range, not safe."""
    arr: Array[Int32, 5] = [10, 20, 30, 40, 50]
    i: Int32 = 0
    while i < len(arr) - 1:
        i += 1
        print(arr[i])  # tpyc: bounds_checked(arr)

def test_while_no_literal_init() -> None:
    """i from Int32() constructor, no literal range -- not safe."""
    arr: Array[Int32, 5] = [10, 20, 30, 40, 50]
    i: Int32 = Int32(0)
    while i < len(arr):
        print(arr[i])  # tpyc: bounds_checked(arr)
        i += 1

def test_while_negative_init() -> None:
    """-1 is TpyUnaryOp (not literal), no range set; condition gives hi_len_of but lo stays None."""
    arr: Array[Int32, 5] = [10, 20, 30, 40, 50]
    i: Int32 = -1
    i += 1
    while i < len(arr):
        print(arr[i])  # tpyc: bounds_checked(arr)
        i += 1

def test_while_list() -> None:
    """Same pattern with list instead of array."""
    lst: list[Int32] = [1, 2, 3]
    i: Int32 = 0
    while i < len(lst):
        print(lst[i])  # tpyc: bounds_safe(lst)
        i += 1

def test_while_bigint_index() -> None:
    """BigInt i = 0 also gets literal range, enabling while-loop elision."""
    lst: list[Int32] = [1, 2, 3]
    i: int = 0
    while i < len(lst):
        print(lst[i])  # tpyc: bounds_safe(lst)
        i += 1

def test_while_post_loop_not_safe() -> None:
    """After while loop exits, loop-body range facts are not retained."""
    arr: Array[Int32, 5] = [1, 2, 3, 4, 5]
    i: Int32 = 0
    while i < len(arr):
        print(arr[i])  # tpyc: bounds_safe(arr)
        i += 1
    i = 0
    print(arr[i])  # tpyc: bounds_checked(arr)

test_while_basic()
test_while_increment_before_access()
test_while_no_literal_init()
test_while_negative_init()
test_while_list()
test_while_bigint_index()
test_while_post_loop_not_safe()
