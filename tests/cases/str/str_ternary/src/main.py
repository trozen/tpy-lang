# Test and/or/ternary expressions with str operands infer string_view.
def test_ternary(a: str, b: str) -> None:
    x = a if True else b  # tpyc: type(StrView)
    print(x)

    y = a if False else b  # tpyc: type(StrView)
    print(y)

    z = "literal_a" if True else "literal_b"  # tpyc: type(StrView)
    print(z)

    # One operand owned: falls back to std::string
    w = a if True else str(42)  # tpyc: type(str)
    print(w)

def test_or(a: str, b: str) -> None:
    x = a or b  # tpyc: type(StrView)
    print(x)

    y = a or "default"  # tpyc: type(StrView)
    print(y)

    # One operand owned: falls back to std::string
    z = a or str(42)  # tpyc: type(str)
    print(z)

def test_and(a: str, b: str) -> None:
    x = a and b  # tpyc: type(StrView)
    print(x)

    y = "prefix" and b  # tpyc: type(StrView)
    print(y)

test_ternary("hello", "world")
test_or("hello", "world")
test_and("hello", "world")
