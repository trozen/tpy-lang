"""Test explicit dunder method calls on container types.

These methods are defined in the module system and should be
correctly resolved by codegen (not emitted as literal C++ method calls).
"""
from tpy import Array, Int32
from tplib import ArrayList


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


# Test ArrayList dunders (user/library type)
al = ArrayList[Int32, 10]()
al.append(Int32(100))
al.append(Int32(200))

# Explicit __len__
print(al.__len__())  # 2

# Explicit __getitem__
print(al.__getitem__(Int32(0)))  # 100

# Explicit __setitem__
al.__setitem__(Int32(0), Int32(111))
print(al.__getitem__(Int32(0)))  # 111


# Test Array dunders
arr: Array[Int32, 3] = [Int32(1), Int32(2), Int32(3)]

# Explicit __len__
print(arr.__len__())  # 3

# Explicit __getitem__
print(arr.__getitem__(Int32(0)))  # 1
print(arr.__getitem__(Int32(2)))  # 3
