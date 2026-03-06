"""Tests that methods work on all container types via unified module lookup."""
from tpy import Int32, Array, Span
from tplib import ArrayList

# ArrayList methods (append, len, subscript)
al = ArrayList[Int32, 8]()
al.append(10)
al.append(20)
al.append(30)
print(len(al))
print(al[0])
print(al[2])

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
