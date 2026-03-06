"""Tests single-element list repeat with repeat_range codegen."""
from tpy import Int32, Array

# Single element repeat - list
zeros: list[Int32] = [0] * 5
print(len(zeros))
print(zeros[0])
print(zeros[4])

# Single element repeat - list (via constructor)
filled: list[Int32] = [42] * 10
print(len(filled))
print(filled[0])
print(filled[9])

# Zero count repeat - produces empty list
empty: list[Int32] = [99] * 0
print(len(empty))

# Variable count
n: Int32 = 3
dynamic: list[Int32] = [7] * n
print(len(dynamic))
print(dynamic[0])
print(dynamic[2])
