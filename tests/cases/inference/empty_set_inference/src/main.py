# Empty set() infers element type from .add(), .discard(), or .remove() calls
from tpy import Int32, Int64

def test_basic() -> None:
    s = set()  # tpyc: type(set[Int32])
    s.add(42)
    s.add(10)
    print(s)

def test_str() -> None:
    s = set()  # tpyc: type(set[str])
    s.add("hello")
    s.add("world")
    print(len(s))
    print("hello" in s)

def test_multiple() -> None:
    s = set()  # tpyc: type(set[Int32])
    s.add(1)
    s.add(2)
    s.add(3)
    print(s)

def test_numeric_widen() -> None:
    s = set()  # tpyc: type(set[Int64])
    s.add(Int32(1))
    s.add(Int64(2))
    print(s)

def takes_set(s: set[Int32]) -> None:
    for x in s:
        print(x)

def test_param_context() -> None:
    s = set()  # tpyc: type(set[Int32])
    s.add(42)
    takes_set(s)

def test_param_only() -> None:
    s = set()  # tpyc: type(set[Int32])
    takes_set(s)

def test_discard_infers() -> None:
    s = set()  # tpyc: type(set[Int32])
    s.discard(42)
    s.add(10)
    print(s)

test_basic()
test_str()
test_multiple()
test_numeric_widen()
test_param_context()
test_param_only()
test_discard_infers()
