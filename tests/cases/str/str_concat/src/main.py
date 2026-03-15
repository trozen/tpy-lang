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

def test_reassign_concat() -> None:
    # x = x + y should use in-place append
    s: String = String("hello")
    s = s + " world"
    print(s)  # hello world
    # str type
    a: str = str("a")
    a = a + "b"
    print(a)  # ab
    # with str() conversion
    n: Int32 = 42
    a = a + str(n)
    print(a)  # ab42

def test_loop_concat() -> None:
    s: String = String("")
    i: Int32 = 0
    while i < 5:
        s += str(i)
        i += 1
    print(s)  # 01234

test_str_concat()
test_str_plus_eq()
test_str_multiconcat()
test_literal_concat()
test_cross_type_concat()
test_reassign_concat()
test_loop_concat()
