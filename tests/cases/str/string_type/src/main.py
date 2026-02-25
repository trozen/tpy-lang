# Test explicit String type (tpy.String -> std::string)
from tpy import Int32, String

def test_string_basic() -> None:
    s: String = String("hello")
    print(s)  # hello
    print(len(s))  # 5

def test_string_from_int() -> None:
    s: String = String(Int32(42))
    print(s)  # 42

def test_string_from_bool() -> None:
    s: String = String(True)
    print(s)  # True

def test_string_getitem() -> None:
    s: String = String("abc")
    print(s[0])  # a
    print(s[-1])  # c

test_string_basic()
test_string_from_int()
test_string_from_bool()
test_string_getitem()
