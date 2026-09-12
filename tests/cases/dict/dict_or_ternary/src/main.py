# and/or/ternary with dict operands: result must be dict[K, V], not bool.
from tpy import int32

def test_or(a: dict[str, int32], b: dict[str, int32]) -> None:
    x = a or b  # tpyc: type(dict[str, int32])
    print(x)

def test_and(a: dict[str, int32], b: dict[str, int32]) -> None:
    x = a and b  # tpyc: type(dict[str, int32])
    print(x)

def test_ternary(a: dict[str, int32], b: dict[str, int32], cond: bool) -> None:
    x = a if cond else b  # tpyc: type(dict[str, int32])
    print(x)

def test_literal_or() -> None:
    x = {"a": int32(1)} or {"b": int32(2)}  # tpyc: type(dict[str, int32])
    print(x)

def test_literal_ternary(cond: bool) -> None:
    x = {"a": int32(1)} if cond else {"b": int32(2)}  # tpyc: type(dict[str, int32])
    print(x)

def test_ternary_alias(a: dict[str, int32], b: dict[str, int32], cond: bool) -> None:
    # x binds to a or b by reference; mutation through x must be visible in original.
    x = a if cond else b
    x["z"] = int32(99)
    print(a)
    print(b)

d1 = {"a": int32(1)}
d2 = {"b": int32(2)}
test_or(d1, d2)
test_and(d1, d2)
test_ternary(d1, d2, True)
test_literal_or()
test_literal_ternary(True)
ta1 = {"a": 1}
tb1 = {"b": 2}
test_ternary_alias(ta1, tb1, True)
ta2 = {"a": 1}
tb2 = {"b": 2}
test_ternary_alias(ta2, tb2, False)
