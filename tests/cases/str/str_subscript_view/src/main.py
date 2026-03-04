# String subscript access on lvalue tuple infers string_view;
# list subscript stays owned (reallocation can invalidate views)
from tpy import Int32

def test_tuple() -> None:
    t = (Int32(10), "hello", True)
    b = t[1]  # tpyc: type(StrView)
    print(b)

def test_list_stays_owned() -> None:
    items: list[str] = ["alpha", "beta"]
    s = items[0]  # tpyc: type(str)
    print(s)

def test_nested_tuple() -> None:
    t = ("outer", ("inner_a", "inner_b"))
    inner = t[1]
    a = inner[0]  # tpyc: type(StrView)
    print(a)

test_tuple()
test_list_stays_owned()
test_nested_tuple()
