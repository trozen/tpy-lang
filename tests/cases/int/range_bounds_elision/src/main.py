# Tests bounds check elision for subscript access when index is provably in-bounds.
from tpy import Array, Int32, Span

def test_for_range_len_array() -> None:
    """for i in range(len(arr)): arr[i] -- index provably in [0, len-1]."""
    arr: Array[Int32, 5] = [10, 20, 30, 40, 50]
    total: Int32 = 0
    for i in range(len(arr)):
        total += arr[i]  # tpyc: bounds_safe(arr)
    print(total)

def test_for_range_len_list() -> None:
    """for i in range(len(lst)): lst[i] -- same for list."""
    lst: list[Int32] = [1, 2, 3]
    total: Int32 = 0
    for i in range(len(lst)):
        total += lst[i]  # tpyc: bounds_safe(lst)
    print(total)

def test_no_elision_unknown_index() -> None:
    """i not from range(len(...)) -- no bounds elision."""
    arr: Array[Int32, 3] = [1, 2, 3]
    i: Int32 = 0
    print(arr[i])  # tpyc: bounds_checked(arr)

def test_no_elision_different_container() -> None:
    """for i in range(len(a)): b[i] -- containers don't match."""
    a: Array[Int32, 3] = [1, 2, 3]
    b: Array[Int32, 3] = [4, 5, 6]
    for i in range(len(a)):
        print(b[i])  # tpyc: bounds_checked(b)

def test_assert_non_negative_only() -> None:
    """Plain integer variable with no range facts -- no elision."""
    arr: Array[Int32, 3] = [1, 2, 3]
    i: Int32 = 1
    x = arr[i]  # tpyc: bounds_checked(arr)
    print(x)

def test_for_range_literal() -> None:
    """for i in range(3): arr[i] -- literal bound, no symbolic len match."""
    arr: Array[Int32, 5] = [1, 2, 3, 4, 5]
    total: Int32 = 0
    for i in range(3):
        total += arr[i]  # tpyc: bounds_checked(arr)
    print(total)

def test_write_subscript_elision() -> None:
    """for i in range(len(arr)): arr[i] = v -- write subscript also elides."""
    arr: Array[Int32, 5] = [0, 0, 0, 0, 0]
    for i in range(len(arr)):
        arr[i] = i * 10  # tpyc: bounds_safe(arr)
    for i in range(len(arr)):
        print(arr[i])  # tpyc: bounds_safe(arr)

def test_no_elision_after_method_call() -> None:
    """lst.pop() invalidates len-based bounds -- no elision after mutation."""
    lst: list[Int32] = [1, 2, 3, 4, 5]
    total: Int32 = 0
    for i in range(len(lst)):
        total += lst[i]  # tpyc: bounds_safe(lst)
    lst.pop()
    i2: Int32 = 0
    print(lst[i2])  # tpyc: bounds_checked(lst)
    print(total)

test_for_range_len_array()
test_for_range_len_list()
test_no_elision_unknown_index()
test_no_elision_different_container()
test_assert_non_negative_only()
test_for_range_literal()
test_write_subscript_elision()
test_no_elision_after_method_call()
