# and/or/ternary with list operands: result must be list[T], not bool.
from tpy import int32

def test_or(a: list[int32], b: list[int32]) -> None:
    x = a or b  # tpyc: type(list[int32])
    print(x)

def test_and(a: list[int32], b: list[int32]) -> None:
    x = a and b  # tpyc: type(list[int32])
    print(x)

def test_ternary(a: list[int32], b: list[int32], cond: bool) -> None:
    x = a if cond else b  # tpyc: type(list[int32])
    print(x)

def test_literal_or() -> None:
    x = [int32(1), int32(2)] or [int32(3), int32(4)]  # tpyc: type(list[int32])
    print(x)

def test_literal_ternary(cond: bool) -> None:
    x = [int32(1), int32(2)] if cond else [int32(3), int32(4)]  # tpyc: type(list[int32])
    print(x)

def test_or_chain(a: list[int32], b: list[int32], c: list[int32]) -> None:
    x = a or b or c  # tpyc: type(list[int32])
    print(x)

def test_local_vars_or() -> None:
    # Local list variables (not params) must also produce list[T], not array.
    a = [int32(1)]  # tpyc: type(list[int32])
    b = [int32(2)]
    x = a or b  # tpyc: type(list[int32])
    print(x)

def test_int_literal_elements_or() -> None:
    # Plain int literals: [1, 2] and [3, 4] have different IntLiteralType elements
    # but should still yield list[int] (default int type), not bool.
    x = [1, 2] or [3, 4]  # tpyc: type(list[int32])
    print(x)

def test_int_literal_elements_ternary(cond: bool) -> None:
    x = [1, 2] if cond else [3, 4]  # tpyc: type(list[int32])
    print(x)

def test_or_alias_first(a: list[int32], b: list[int32]) -> None:
    # x binds to a (non-empty); mutating a must be visible through x.
    x = a or b
    a.append(int32(99))
    print(x)  # must include 99

def test_or_alias_second(a: list[int32], b: list[int32]) -> None:
    # a is empty so x binds to b; mutating b must be visible through x.
    x = a or b
    b.append(int32(99))
    print(x)  # must include 99

def test_and_alias(a: list[int32], b: list[int32]) -> None:
    # x = a and b returns b when a is truthy; mutating b must be visible through x.
    x = a and b
    b.append(int32(99))
    print(x)  # must include 99

def test_or_chain_alias(a: list[int32], b: list[int32], c: list[int32]) -> None:
    # a is empty, b is non-empty, so x binds to b.
    x = a or b or c
    b.append(int32(99))
    print(x)  # must include 99

def test_ternary_alias(a: list[int32], b: list[int32], cond: bool) -> None:
    # x binds to a or b by reference; mutation through x must be visible in original.
    x = a if cond else b
    x.append(int32(99))
    print(a)
    print(b)

test_or([int32(1), int32(2)], [int32(3), int32(4)])
test_and([int32(1), int32(2)], [int32(3), int32(4)])
test_ternary([int32(1), int32(2)], [int32(3), int32(4)], True)
test_literal_or()
test_literal_ternary(True)
test_or_chain([int32(1)], [int32(2)], [int32(3)])
test_local_vars_or()
test_int_literal_elements_or()
test_int_literal_elements_ternary(True)
test_or_alias_first([int32(1)], [int32(2)])
test_or_alias_second([], [int32(2)])
test_and_alias([int32(1)], [int32(2)])
test_or_chain_alias([], [int32(2)], [int32(3)])
test_ternary_alias([int32(1)], [int32(2)], True)
test_ternary_alias([int32(1)], [int32(2)], False)
