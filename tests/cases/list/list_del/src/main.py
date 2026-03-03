# del lst[i] -- remove element at index from list
from tpy import Int32

def test_basic() -> None:
    items: list[Int32] = [10, 20, 30, 40, 50]
    print(items)
    del items[1]
    print(items)
    print(len(items))

def test_negative_index() -> None:
    items: list[Int32] = [1, 2, 3, 4]
    del items[-1]
    print(items)

def test_first_element() -> None:
    items: list[Int32] = [10, 20, 30]
    del items[0]
    print(items)

test_basic()
test_negative_index()
test_first_element()
