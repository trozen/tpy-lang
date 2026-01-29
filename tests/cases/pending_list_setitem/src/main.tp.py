"""Test that __setitem__ marks a pending list as mutated.

Without this fix, a function-local pending list like [1, 2, 3] would resolve
to Array (not list) when only __setitem__ is called, leading to invalid C++.
"""
from tpy import Int32

def test_setitem_mutation():
    # Function-local: should resolve to list (not Array) because __setitem__ mutates
    items = [Int32(1), Int32(2), Int32(3)]
    items.__setitem__(Int32(0), Int32(99))
    print(items[Int32(0)])  # 99
    print(items[Int32(1)])  # 2
    print(items[Int32(2)])  # 3

test_setitem_mutation()
