# Test PendingStrType resolves to StrView for view-safe sources
from tpy import StrView

def test_literal() -> None:
    s = "hello"
    print(s)
    print(len(s))

def test_param(msg: str) -> None:
    s = msg
    print(s)

def test_strview_source() -> None:
    sv: StrView = StrView("view")
    s = sv
    print(s)

test_literal()
test_param("from param")
test_strview_source()
