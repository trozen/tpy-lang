# Test that generic inference prefers StrView for string literal arguments.

def identity[T](x: T) -> T:
    return x

def first[T](a: T, b: T) -> T:
    return a

def test_literal_infer_strview() -> None:
    x = identity("hello")  # tpyc: type(/StrView|PendingStr/)
    print(x)
    y = first("hello", "world")  # tpyc: type(/StrView|PendingStr/)
    print(y)

def test_explicit_no_downgrade() -> None:
    x = identity[str]("hello")
    print(x)

def make_str() -> str:
    return "hi"

def test_non_literal_no_downgrade() -> None:
    x = first(make_str(), make_str())  # tpyc: type(str)
    print(x)

test_literal_infer_strview()
test_explicit_no_downgrade()
test_non_literal_no_downgrade()
