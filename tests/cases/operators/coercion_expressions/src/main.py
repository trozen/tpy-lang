"""Tests type coercions with expressions (not just simple variables).

Ensures coercions work correctly when the source is:
- Binary operation result
- Method call result
- Field access
- Nested expressions
"""
from tpy import Int32, Ptr, readonly

class Counter:
    value: int

    def __init__(self, v: int) -> None:
        self.value = v

    def get(self) -> int:
        return self.value

    def add(self, x: int) -> int:
        return self.value + x


class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


class Container:
    pt: Point

    def __init__(self, x: Int32, y: Int32) -> None:
        self.pt = Point(x, y)


class Outer:
    inner: Container

    def __init__(self, x: Int32, y: Int32) -> None:
        self.inner = Container(x, y)


# --- BigInt expression -> Int32 ---

def return_expr_as_int32(a: int, b: int) -> Int32:
    return a + b  # Expression result (BigInt) -> Int32

def take_int32(n: Int32) -> Int32:
    return n * 2


def test_bigint_expr_to_int32() -> None:
    print("BigInt expressions -> Int32:")

    a: int = 10
    b: int = 20

    # Binary op result in return
    result: Int32 = return_expr_as_int32(a, b)
    print(result)  # 30

    # Binary op result in function argument
    print(take_int32(a + b))  # 60

    # Binary op result in variable declaration
    sum_val: Int32 = a + b + 5
    print(sum_val)  # 35

    # Binary op result in assignment
    sum_val = a * b
    print(sum_val)  # 200

    # Method call result -> Int32
    c: Counter = Counter(100)
    val: Int32 = c.get()
    print(val)  # 100

    # Method call with arg result -> Int32
    val = c.add(50)
    print(val)  # 150


# --- Field access -> Ptr ---

def modify_via_ptr(p: Ptr[Point]) -> None:
    p.x = p.x + 100

def read_via_const_ptr(p: Ptr[readonly[Point]]) -> Int32:
    return p.x + p.y


def test_field_access_to_ptr() -> None:
    print("Field access -> Ptr:")

    cont: Container = Container(5, 10)

    # Field access -> Ptr in function call
    modify_via_ptr(cont.pt)
    print(cont.pt.x)  # 105

    # Field access -> Ptr[readonly[...]] in function call
    print(read_via_const_ptr(cont.pt))  # 115


def test_nested_field_to_ptr() -> None:
    print("Nested field -> Ptr:")

    outer: Outer = Outer(7, 8)

    # Nested field access -> Ptr
    modify_via_ptr(outer.inner.pt)
    print(outer.inner.pt.x)  # 107

    # Nested field access -> Ptr[readonly[...]]
    print(read_via_const_ptr(outer.inner.pt))  # 115


def test_literal_expr_to_int32() -> None:
    print("Literal expressions -> Int32:")

    # Literal arithmetic assigned to Int32 (should use Int32 ops)
    x: Int32 = 10 + 20 + 30
    print(x)  # 60

    # Nested literal expression
    y: Int32 = (5 + 5) * (2 + 3)
    print(y)  # 50

    # Mixed literal and variable
    aa: Int32 = 100
    bb: Int32 = aa + 50  # Int32 + literal -> Int32
    print(bb)  # 150


# --- Run all tests ---

test_bigint_expr_to_int32()
test_field_access_to_ptr()
test_nested_field_to_ptr()
test_literal_expr_to_int32()
