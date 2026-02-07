"""Test explicit dunder method calls on container types.

These methods are defined in the module system and should be
correctly resolved by codegen (not emitted as literal C++ method calls).
"""
from tpy import StaticList, Array, Int32


# Test list dunders
items: list[Int32] = []
items.append(Int32(10))
items.append(Int32(20))
items.append(Int32(30))

# Explicit __len__
print(items.__len__())  # 3

# Explicit __getitem__
print(items.__getitem__(Int32(0)))  # 10
print(items.__getitem__(Int32(1)))  # 20

# Explicit __setitem__
items.__setitem__(Int32(1), Int32(99))
print(items.__getitem__(Int32(1)))  # 99


# Test StaticList dunders
sl = StaticList[Int32, 10]()
sl.append(Int32(100))
sl.append(Int32(200))

# Explicit __len__
print(sl.__len__())  # 2

# Explicit __getitem__
print(sl.__getitem__(Int32(0)))  # 100

# Explicit __setitem__
sl.__setitem__(Int32(0), Int32(111))
print(sl.__getitem__(Int32(0)))  # 111


# Test Array dunders
arr: Array[Int32, 3] = [Int32(1), Int32(2), Int32(3)]

# Explicit __len__
print(arr.__len__())  # 3

# Explicit __getitem__
print(arr.__getitem__(Int32(0)))  # 1
print(arr.__getitem__(Int32(2)))  # 3
