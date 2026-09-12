"""Test that __setitem__ marks a pending list as mutated.

Without this fix, a function-local pending list like [1, 2, 3] would resolve
to Array (not list) when only __setitem__ is called, leading to invalid C++.
"""
from tpy import int32

def test_setitem_mutation():
    # Function-local: should resolve to list (not Array) because __setitem__ mutates
    items = [int32(1), int32(2), int32(3)]
    items.__setitem__(int32(0), int32(99))
    print(items[int32(0)])  # 99
    print(items[int32(1)])  # 2
    print(items[int32(2)])  # 3

test_setitem_mutation()
