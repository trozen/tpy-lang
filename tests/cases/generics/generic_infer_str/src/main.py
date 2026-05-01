# Test that string literals in generic-T positions infer as str (storage form),
# not auto-downgraded to StrView. StrView is only used when requested explicitly.
from tpy import StrView

def identity[T](x: T) -> T:
    return x

def first[T](a: T, b: T) -> T:
    return a

def test_literal_infers_str() -> None:
    x = identity("hello")  # tpyc: type(/str|PendingStr/)
    print(x)
    y = first("hello", "world")  # tpyc: type(/str|PendingStr/)
    print(y)

def test_explicit_str() -> None:
    x = identity[str]("hello")
    print(x)

def test_explicit_strview() -> None:
    x: StrView = identity[StrView]("hello")
    print(x)

def make_str() -> str:
    return "hi"

def test_non_literal_str() -> None:
    x = first(make_str(), make_str())  # tpyc: type(str)
    print(x)

test_literal_infers_str()
test_explicit_str()
test_explicit_strview()
test_non_literal_str()
