# auto_own[Self] generates borrowing + consuming overloads from a single method.
# The consuming clone gets self: Own[Self] and auto_own[T] -> Own[T] in return type.
# Ownership propagates through field access: self.field is Own[T] in the consuming clone.
from typing import Self
from tpy import Int32, auto_own, copy

class Node:
    val: Int32
    def __init__(self, val: Int32) -> None:
        self.val = val

class Pair[T]:
    first_val: T
    second_val: T
    def __init__(self, a: T, b: T) -> None:
        self.first_val = copy(a)
        self.second_val = copy(b)

    def first(self: auto_own[Self]) -> auto_own[T]:
        return self.first_val

def test_borrowing() -> None:
    p = Pair[Node](Node(10), Node(20))
    x = p.first()
    print(x.val)
    print(p.second_val.val)

def test_consuming() -> None:
    p = Pair[Node](Node(30), Node(40))
    x = p.first()
    print(x.val)

test_borrowing()
test_consuming()
