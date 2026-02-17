# Generic helper functions for cross-module import testing
from tpy import Int32

def first[T](items: list[T]) -> T:
    return items[0]

def length[T](items: list[T]) -> Int32:
    return len(items)
