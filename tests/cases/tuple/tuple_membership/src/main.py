# Tuple membership: x in (a, b, c) lowers to x == a || x == b || x == c.
from tpy import Int32

def test_int_membership() -> None:
    x: Int32 = 17
    if x in (1, 17, 42):
        print("found 17")
    if x not in (1, 2, 3):
        print("17 not in small set")
    if x not in (17, 42):
        print("should not print")

def test_str_membership() -> None:
    s = "hello"
    if s in ("hello", "world"):
        print("found hello")
    if s not in ("foo", "bar"):
        print("hello not in foo/bar")

def test_single_element() -> None:
    x: Int32 = 5
    if x in (5,):
        print("single match")
    if x not in (3,):
        print("single non-match")

call_count: Int32 = 0

def get_val() -> Int32:
    global call_count
    call_count = call_count + 1
    return Int32(17)

def test_call_lhs() -> None:
    global call_count
    call_count = 0
    if get_val() in (1, 17, 42):
        print("call found")
    print(call_count)

def main() -> None:
    test_int_membership()
    test_str_membership()
    test_single_element()
    test_call_lhs()

main()
