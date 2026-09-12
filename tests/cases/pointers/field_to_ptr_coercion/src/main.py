from tpy import int32, Ptr, Array, readonly

class Inner:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x

class Outer:
    inner: Inner
    def __init__(self, x: int32) -> None:
        self.inner = Inner(x)

def modify_inner(p: Ptr[Inner]) -> None:
    p.x = 999

def read_inner(p: Ptr[readonly[Inner]]) -> int32:
    return p.x

def test_field_to_ptr() -> None:
    outer: Outer = Outer(42)
    # obj.field -> Ptr coercion
    modify_inner(outer.inner)
    print(outer.inner.x)

def test_field_to_const_ptr() -> None:
    outer: Outer = Outer(100)
    # obj.field -> Ptr[readonly[...]] coercion
    result: int32 = read_inner(outer.inner)
    print(result)

def test_subscript_to_ptr() -> None:
    arr: Array[Inner, 3] = [Inner(1), Inner(2), Inner(3)]
    # arr[i] -> Ptr coercion
    modify_inner(arr[1])
    print(arr[1].x)

# Run tests
print("=== field to ptr ===")
test_field_to_ptr()
print("=== field to const ptr ===")
test_field_to_const_ptr()
print("=== subscript to ptr ===")
test_subscript_to_ptr()
