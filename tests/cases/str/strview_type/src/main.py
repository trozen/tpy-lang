# Test explicit StrView type (tpy.StrView -> std::string_view)
from tpy import StrView

def test_strview_basic() -> None:
    s: StrView = StrView("hello")
    print(s)  # hello
    print(len(s))  # 5

def test_strview_getitem() -> None:
    s: StrView = StrView("abc")
    print(s[0])  # a
    print(s[-1])  # c

test_strview_basic()
test_strview_getitem()
