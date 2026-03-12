# and/or/ternary with set operands: result must be set[T], not bool.
from tpy import Int32

def test_or(a: set[Int32], b: set[Int32]) -> None:
    x = a or b  # tpyc: type(set[Int32])
    print(x)

def test_and(a: set[Int32], b: set[Int32]) -> None:
    x = a and b  # tpyc: type(set[Int32])
    print(x)

def test_ternary(a: set[Int32], b: set[Int32], cond: bool) -> None:
    x = a if cond else b  # tpyc: type(set[Int32])
    print(x)

def test_literal_or() -> None:
    x = {Int32(1), Int32(2)} or {Int32(3), Int32(4)}  # tpyc: type(set[Int32])
    print(x)

def test_literal_ternary(cond: bool) -> None:
    x = {Int32(1), Int32(2)} if cond else {Int32(3), Int32(4)}  # tpyc: type(set[Int32])
    print(x)

s1 = {Int32(1), Int32(2)}
s2 = {Int32(3), Int32(4)}
test_or(s1, s2)
test_and(s1, s2)
test_ternary(s1, s2, True)
test_literal_or()
test_literal_ternary(True)
