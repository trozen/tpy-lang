# @overload return coercion: sema coerces against impl's union return type,
# but codegen must strip wrong-target coercions for each stub.
# Tests multiple numeric/string coercion paths.
from typing import overload
from tpy import int32, int64

class A:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

class B:
    y: float
    def __init__(self, y: float) -> None:
        self.y = y

class C:
    z: int64
    def __init__(self, z: int64) -> None:
        self.z = z

# Test 1: int32 returned where stub says -> int, union has float first
@overload
def get_val(obj: A) -> int: ...  # tpyc: ok

@overload
def get_val(obj: B) -> float: ...  # tpyc: ok

def get_val(obj: A | B) -> float | int:
    if isinstance(obj, A):
        return obj.x  # int32, stub -> int (not float)
    else:
        return obj.y

# Test 2: int64 returned where stub says -> int, union has float first
@overload
def get_big(obj: C) -> int: ...  # tpyc: ok

@overload
def get_big(obj: B) -> float: ...  # tpyc: ok

def get_big(obj: C | B) -> float | int:
    if isinstance(obj, C):
        return obj.z  # int64, stub -> int (not float)
    else:
        return obj.y

# Test 3: int32 returned where stub says -> int64, union has float first
@overload
def get_wide(obj: A) -> int64: ...  # tpyc: ok

@overload
def get_wide(obj: B) -> float: ...  # tpyc: ok

def get_wide(obj: A | B) -> float | int64:
    if isinstance(obj, A):
        return obj.x  # int32, stub -> int64 (not float)
    else:
        return obj.y

# Test 4: int32 returned where stub says -> float, union has int first
@overload
def get_cast(obj: A) -> float: ...  # tpyc: ok

@overload
def get_cast(obj: C) -> int: ...  # tpyc: ok

def get_cast(obj: A | C) -> int | float:
    if isinstance(obj, A):
        return obj.x  # int32, stub -> float (not int)
    else:
        return obj.z

def main() -> None:
    a = A(int32(42))
    b = B(3.14)
    c = C(int64(100))

    print(get_val(a))
    print(get_val(b))
    print(get_big(c))
    print(get_big(b))
    print(get_wide(a))
    print(get_wide(b))
    # int() normalizes output: TPy returns float (via stub), CPython returns int32
    print(int(get_cast(a)))
    print(get_cast(c))

main()
