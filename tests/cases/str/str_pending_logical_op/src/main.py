# PendingStrType propagates through and/or and ternary when both sides are
# view-compatible, so the result resolves to string_view instead of string.
from tpy import StrView

def test_or(a: str, b: str) -> None:
    x = a or b  # tpyc: type(StrView)
    print(x)

def test_and(a: str, b: str) -> None:
    x = a and b  # tpyc: type(StrView)
    print(x)

def test_ternary(a: str, b: str, cond: bool) -> None:
    x = a if cond else b  # tpyc: type(StrView)
    print(x)

def test_literal_or() -> None:
    x = "hello" or "world"  # tpyc: type(StrView)
    print(x)

def test_or_right_promotes() -> None:
    # When the right-hand operand later promotes to std::string (via augassign),
    # the result x must also promote -- it might point to b's buffer at runtime.
    a = "hello"
    b = "world"
    x = a or b  # tpyc: type(str)
    b += "!"
    print(x)

def test_ternary_right_promotes() -> None:
    a = "hello"
    b = "world"
    x = a if True else b  # tpyc: type(str)
    b += "!"
    print(x)

def test_or_chain(a: str, b: str, c: str) -> None:
    # Three-operand chain: (a or b) or c -- parsed left-associatively.
    x = a or b or c  # tpyc: type(StrView)
    print(x)

def test_literal_or_chain() -> None:
    x = "foo" or "bar" or "baz"  # tpyc: type(StrView)
    print(x)

def test_or_chain_third_promotes() -> None:
    # Promotion must propagate from the third operand too, not just left/right.
    a = "hello"
    b = "world"
    c = "!"
    x = a or b or c  # tpyc: type(str)
    c += "?"
    print(x)

test_or("hello", "world")
test_and("hello", "world")
test_ternary("hello", "world", True)
test_literal_or()
test_or_right_promotes()
test_ternary_right_promotes()
test_or_chain("hello", "world", "!")
test_literal_or_chain()
test_or_chain_third_promotes()
