# TypedDict .get() and "key" in td for CPython-compatible access patterns
from typing import TypedDict, Optional
from tpy import Int32

class Required(TypedDict):
    name: str
    age: Int32

class NullableField(TypedDict):
    name: Optional[str]
    count: Int32

class Partial(TypedDict, total=False):
    name: str
    age: Int32

def test_in_total_true() -> None:
    r = Required(name="Alice", age=Int32(30))
    print("name" in r)   # always True for total=True
    print("age" in r)
    print("name" not in r)  # always False

def test_in_nullable_field() -> None:
    # total=True with Optional[T] field -- key is always present
    n = NullableField(name=None, count=Int32(1))
    print("name" in n)   # True (field present, even though value is None)
    print("count" in n)  # True

def test_in_total_false() -> None:
    full = Partial(name="Bob", age=Int32(25))
    empty = Partial()
    partial = Partial(name="Carol")
    print("name" in full)      # True
    print("age" in full)       # True
    print("name" in empty)     # False
    print("age" in empty)      # False
    print("name" in partial)   # True
    print("age" in partial)    # False
    print("name" not in empty) # True

def test_get_total_true() -> None:
    r = Required(name="Alice", age=Int32(30))
    # get without default -> Optional[T]
    v = r.get("name")
    if v is not None:
        print(v)
    # get with default -> T
    print(r.get("name", "default"))
    print(r.get("age", Int32(0)))

def test_get_nullable_field() -> None:
    # total=True with Optional[T] field -- get returns Optional[T]
    n = NullableField(name=None, count=Int32(1))
    v = n.get("name")
    if v is not None:
        print("unexpected")
    else:
        print("None")
    # get with default -- field is present with None value, returns None (not default)
    print(n.get("name", "fallback"))
    print(n.get("count", Int32(0)))

def test_get_total_false() -> None:
    full = Partial(name="Bob", age=Int32(25))
    empty = Partial()
    # get without default -> Optional[T] (may be None)
    v = full.get("name")
    if v is not None:
        print(v)
    v2 = empty.get("name")
    if v2 is not None:
        print("unexpected")
    else:
        print("None")
    # get with default -> T
    print(full.get("name", "fallback"))
    print(empty.get("name", "fallback"))
    print(full.get("age", Int32(99)))
    print(empty.get("age", Int32(99)))

def test_get_str_param_default(s: str) -> None:
    # Ensure string_view default compiles with value_or
    empty = Partial()
    print(empty.get("name", s))

test_in_total_true()
test_in_nullable_field()
test_in_total_false()
test_get_total_true()
test_get_nullable_field()
test_get_total_false()
test_get_str_param_default("param_default")
