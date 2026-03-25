# auto_own[Self] generates borrowing + consuming overloads from a single method.
# Consuming dispatch only happens for __iter__ via for-loop detection;
# general method calls always use the borrowing overload.
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

def test_last_use() -> None:
    p = Pair[Node](Node(30), Node(40))
    x = p.first()  # borrowing even at last use (not __iter__)
    print(x.val)

test_borrowing()
test_last_use()
