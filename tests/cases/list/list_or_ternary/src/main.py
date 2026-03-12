# and/or/ternary with list operands: result must be list[T], not bool.
from tpy import Int32

def test_or(a: list[Int32], b: list[Int32]) -> None:
    x = a or b  # tpyc: type(list[Int32])
    print(x)

def test_and(a: list[Int32], b: list[Int32]) -> None:
    x = a and b  # tpyc: type(list[Int32])
    print(x)

def test_ternary(a: list[Int32], b: list[Int32], cond: bool) -> None:
    # NOTE: In CPython, x is a reference to a or b; here x is a value copy.
    # The test only checks print output, so CPython output still matches.
    x = a if cond else b  # tpyc: type(list[Int32])
    print(x)

def test_literal_or() -> None:
    x = [Int32(1), Int32(2)] or [Int32(3), Int32(4)]  # tpyc: type(list[Int32])
    print(x)

def test_literal_ternary(cond: bool) -> None:
    x = [Int32(1), Int32(2)] if cond else [Int32(3), Int32(4)]  # tpyc: type(list[Int32])
    print(x)

def test_or_chain(a: list[Int32], b: list[Int32], c: list[Int32]) -> None:
    x = a or b or c  # tpyc: type(list[Int32])
    print(x)

def test_local_vars_or() -> None:
    # Local list variables (not params) must also produce list[T], not array.
    a = [Int32(1)]  # tpyc: type(list[Int32])
    b = [Int32(2)]
    x = a or b  # tpyc: type(list[Int32])
    print(x)

def test_int_literal_elements_or() -> None:
    # Plain int literals: [1, 2] and [3, 4] have different IntLiteralType elements
    # but should still yield list[int] (default int type), not bool.
    x = [1, 2] or [3, 4]  # tpyc: type(list[Int32])
    print(x)

def test_int_literal_elements_ternary(cond: bool) -> None:
    x = [1, 2] if cond else [3, 4]  # tpyc: type(list[Int32])
    print(x)

def test_or_alias_first(a: list[Int32], b: list[Int32]) -> None:
    # x binds to a (non-empty); mutating a must be visible through x.
    x = a or b
    a.append(Int32(99))
    print(x)  # must include 99

def test_or_alias_second(a: list[Int32], b: list[Int32]) -> None:
    # a is empty so x binds to b; mutating b must be visible through x.
    x = a or b
    b.append(Int32(99))
    print(x)  # must include 99

def test_and_alias(a: list[Int32], b: list[Int32]) -> None:
    # x = a and b returns b when a is truthy; mutating b must be visible through x.
    x = a and b
    b.append(Int32(99))
    print(x)  # must include 99

def test_or_chain_alias(a: list[Int32], b: list[Int32], c: list[Int32]) -> None:
    # a is empty, b is non-empty, so x binds to b.
    x = a or b or c
    b.append(Int32(99))
    print(x)  # must include 99

test_or([Int32(1), Int32(2)], [Int32(3), Int32(4)])
test_and([Int32(1), Int32(2)], [Int32(3), Int32(4)])
test_ternary([Int32(1), Int32(2)], [Int32(3), Int32(4)], True)
test_literal_or()
test_literal_ternary(True)
test_or_chain([Int32(1)], [Int32(2)], [Int32(3)])
test_local_vars_or()
test_int_literal_elements_or()
test_int_literal_elements_ternary(True)
test_or_alias_first([Int32(1)], [Int32(2)])
test_or_alias_second([], [Int32(2)])
test_and_alias([Int32(1)], [Int32(2)])
test_or_chain_alias([], [Int32(2)], [Int32(3)])
