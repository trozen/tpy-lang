"""Test inherited builtin methods with multiple overloads.

When a user class inherits from a builtin type like list,
methods with multiple overloads (like pop) should all be accessible.
"""
from tpy import int32

# User class inheriting from list
class MyList(list[int32]):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

ml: MyList = MyList("test")
ml.append(10)
ml.append(20)
ml.append(30)
ml.append(40)

# Test pop() with no args - should remove and return last element
last: int32 = ml.pop()
print(last)  # 40

# Test pop(index) - should remove and return element at index
first: int32 = ml.pop(0)
print(first)  # 10

# Remaining elements should be [20, 30]
print(ml[0])  # 20
print(ml[1])  # 30
print(len(ml))  # 2

# Also test that regular list pop overloads work (sanity check)
items: list[int32] = [1, 2, 3, 4, 5]
print(items.pop())  # 5 - pop last
print(items.pop(0))  # 1 - pop first
print(len(items))  # 3
