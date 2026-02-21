"""Regression tests for generic functions with various edge cases."""
from tpy import Int32, StaticList, Array

# Uppercase generic function (tests routing: function vs type)
def First[T](items: list[T]) -> T:
    return items[0]

# Generic function with record type
class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32):
        self.x = x
        self.y = y

def get_item[T](items: list[T], idx: Int32) -> T:
    return items[idx]

# Test 1: Uppercase generic function (inferred) - critical for routing test
nums = [10, 20, 30]
print(First(nums))

# Test 2: Builtin type constructor in same file (tests routing still works)
sl: StaticList[Int32, 3] = StaticList[Int32, 3]([1, 2, 3])
print(len(sl))

# Test 3: list constructor in same file
items = list[Int32]()
items.append(100)
print(len(items))

# Test 4: Same generic function with different types
strs = ["hello", "world"]
print(First(strs))

# Test 5: Generic function with record type (inferred)
points = [Point(1, 2), Point(3, 4)]
p = get_item(points, Int32(0))
print(p.x)

# Test 6: Chained generic calls
first_num = First([10, 20, 30])
second_num = First([first_num, 40, 50])
print(second_num)

# Test 7: Generic function in expression context
result = First([5, 6, 7]) + 10
print(result)
