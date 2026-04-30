# StrView as dict/set key: parallel to BytesView. The view_key_target
# helper in codegen pins string literals (already const-char-pointer with
# static storage) so the stored string_view doesn't dangle. Without the
# threading, sema/codegen still happens to work because string literals
# have static storage anyway -- but we exercise it explicitly to lock
# the StrView leg of the helper against regression.
from tpy import StrView

def test_set() -> None:
    s: set[StrView] = set()
    s.add("hello")
    s.add("world")
    s.add("hello")
    print(len(s))
    print("hello" in s)
    print("missing" in s)

def test_dict() -> None:
    d: dict[StrView, int] = {}
    d["alice"] = 1
    d["bob"] = 2
    d["alice"] = 10
    print(d["alice"])
    print(d["bob"])
    print(len(d))
    print("alice" in d)
    print("missing" in d)

test_set()
test_dict()
