# and/or/ternary with set operands: result must be set[T], not bool.
from tpy import int32

def test_or(a: set[int32], b: set[int32]) -> None:
    x = a or b  # tpyc: type(set[int32])
    print(x)

def test_and(a: set[int32], b: set[int32]) -> None:
    x = a and b  # tpyc: type(set[int32])
    print(x)

def test_ternary(a: set[int32], b: set[int32], cond: bool) -> None:
    x = a if cond else b  # tpyc: type(set[int32])
    print(x)

def test_literal_or() -> None:
    x = {int32(1), int32(2)} or {int32(3), int32(4)}  # tpyc: type(set[int32])
    print(x)

def test_literal_ternary(cond: bool) -> None:
    x = {int32(1), int32(2)} if cond else {int32(3), int32(4)}  # tpyc: type(set[int32])
    print(x)

def test_ternary_alias(a: set[int32], b: set[int32], cond: bool) -> None:
    # x binds to a or b by reference; mutation through x must be visible in original.
    x = a if cond else b
    x.add(int32(99))
    print(99 in a)  # True if cond, else False
    print(99 in b)  # False if cond, else True

s1 = {int32(1), int32(2)}
s2 = {int32(3), int32(4)}
test_or(s1, s2)
test_and(s1, s2)
test_ternary(s1, s2, True)
test_literal_or()
test_literal_ternary(True)
sa1 = {1, 2}
sb1 = {3, 4}
test_ternary_alias(sa1, sb1, True)
sa2 = {1, 2}
sb2 = {3, 4}
test_ternary_alias(sa2, sb2, False)
