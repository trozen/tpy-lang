"""Tests that methods work on all container types via unified module lookup."""
from tpy import Int32, StaticList, Array, Span

# StaticList methods (append, len, subscript)
sl: StaticList[Int32, 8] = StaticList[Int32, 8]()
sl.append(10)
sl.append(20)
sl.append(30)
print(len(sl))
print(sl[0])
print(sl[2])

# Array methods (subscript, len)
arr: Array[Int32, 3] = [100, 200, 300]
print(len(arr))
print(arr[0])
print(arr[2])
arr[1] = 250
print(arr[1])

# Span methods (via function parameter)
def span_ops(sp: Span[Int32]) -> None:
    print(len(sp))
    print(sp[0])
    print(sp[1])

span_ops(arr)

# list methods
nums: list[Int32] = [1, 2, 3]
nums.append(4)
print(len(nums))
print(nums[0])
print(nums[3])
nums.pop()
print(len(nums))
