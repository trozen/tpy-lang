# Test passing to a String param promotes PendingStr to str
from tpy import String

def takes_string(s: String) -> None:
    print(s)

def test_string_param() -> None:
    s = "hello"
    takes_string(s)
    print(s)

test_string_param()
