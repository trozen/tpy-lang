# Test string concatenation with +, += operators
from tpy import Int32, String

def test_str_concat() -> None:
    a: str = "hello"
    b: str = " world"
    c: String = a + b
    print(c)  # hello world

def test_str_plus_eq() -> None:
    s: String = String("hello")
    s += " world"
    print(s)  # hello world

def test_str_multiconcat() -> None:
    s: String = String("a") + "b" + "c"
    print(s)  # abc

def test_literal_concat() -> None:
    s: String = "foo" + "bar"
    print(s)  # foobar

def test_cross_type_concat() -> None:
    a: str = str("hello")
    b: String = String(" world")
    # str + String
    print(a + b)  # hello world
    # String + str
    print(b + a)  #  worldhello
    # str += with str target
    a += " end"
    print(a)  # hello end

test_str_concat()
test_str_plus_eq()
test_str_multiconcat()
test_literal_concat()
test_cross_type_concat()
