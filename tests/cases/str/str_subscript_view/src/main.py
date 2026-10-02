# A str subscript read: an element of a tuple NAME the function binds lends a
# string_view, while a list element binds an owned std::string.
from tpy import int32

def test_tuple() -> None:
    t = (int32(10), "hello", True)
    b = t[1]  # tpyc: type(StrView)
    print(b)

def test_list_view() -> None:
    items: list[str] = ["alpha", "beta"]
    s = items[0]  # tpyc: type(str)
    print(s)

def test_nested_tuple() -> None:
    t = ("outer", ("inner_a", "inner_b"))
    inner = t[1]
    a = inner[0]  # tpyc: type(StrView)
    print(a)

test_tuple()
test_list_view()
test_nested_tuple()
