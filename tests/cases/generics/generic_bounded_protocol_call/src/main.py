# Test calling protocol methods on bounded type parameters
from __future__ import annotations
from typing import Protocol, Self, Sized
from tpy import int32, Own, Comparable

# Test 1: Builtin protocol (Sized) method call inside generic function
def get_length[T: Sized](item: T) -> int32:
    return len(item)

# Test 2: User-defined protocol
class Stringable(Protocol):
    def to_str(self) -> str: ...

# Test 3: Generic function with user protocol bound
def stringify[T: Stringable](item: T) -> str:
    return item.to_str()

# Test 4: Generic class with bounded type parameter
class Printer[T: Stringable]:
    def get_str(self, item: T) -> str:
        # Call protocol method on bounded type parameter inside generic class method
        return item.to_str()

# Test 5: Type satisfying multiple protocols (Stringable and Sized)
class MyValue:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v

    def to_str(self) -> str:
        return "value"

    def __len__(self) -> int32:
        return self.val

# Test 6: Multiple type params with different bounds
def process_both[T: Sized, U: Stringable](a: T, b: U) -> int32:
    print(b.to_str())
    return len(a)

# Test 7: Protocol with multiple methods
class MultiMethod(Protocol):
    def get_name(self) -> str: ...
    def get_value(self) -> int32: ...

def use_multi[T: MultiMethod](item: T) -> None:
    print(item.get_name())
    print(item.get_value())

# Test 8: Nested bounded calls (passing bounded param to another bounded function)
def inner_len[T: Sized](x: T) -> int32:
    return len(x)

def outer_len[T: Sized](x: T) -> int32:
    return inner_len(x)

# Test 9: User protocol with Self in signature - Self should resolve to T, not the protocol
class Clonable(Protocol):
    def clone(self) -> Own[Self]: ...

def clone_it[T: Clonable](item: T) -> Own[T]:
    return item.clone()  # Return type should be Own[T], not Own[Clonable]

# Test 10: Builtin protocol with Self (Comparable has __lt__(Self) -> bool)
def is_less[T: Comparable](a: T, b: T) -> bool:
    return a < b  # Uses __lt__ which takes Self parameter

# Another type satisfying Stringable
class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def to_str(self) -> str:
        return "Point"

# Type satisfying MultiMethod protocol
class Widget:
    name: str
    val: int32

    def __init__(self, name: str, val: int32) -> None:
        self.name = name
        self.val = val

    def get_name(self) -> str:
        return self.name

    def get_value(self) -> int32:
        return self.val

# Type satisfying Clonable protocol
class Box:
    value: int32

    def __init__(self, v: int32) -> None:
        self.value = v

    def clone(self) -> Own[Box]:
        return Box(self.value)

def main() -> None:
    # Test 1: len() on list (builtin Sized)
    items = [1, 2, 3, 4, 5]
    print(get_length(items))  # 5

    # Test 1b: len() on str (builtin Sized)
    msg = "hello"
    print(get_length(msg))  # 5

    # Test 2: User-defined protocol method
    v = MyValue(42)
    print(stringify(v))  # value

    # Test 3: MyValue satisfies Sized too (has __len__)
    print(get_length(v))  # 42

    # Test 4: Generic class calling method on bounded type param
    printer = Printer[MyValue]()
    print(printer.get_str(v))  # value

    # Test 4b: Same generic class with different type
    point_printer = Printer[Point]()
    print(point_printer.get_str(Point(10, 20)))  # Point

    # Test 6: Multiple type params with different bounds
    print(process_both(items, v))  # value, then 5

    # Test 7: Protocol with multiple methods
    w = Widget("test", 99)
    use_multi(w)  # test, then 99

    # Test 8: Nested bounded calls
    print(outer_len(items))  # 5

    # Test 9: User protocol with Self - clone returns T (Box), not Clonable
    box = Box(123)
    cloned = clone_it(box)  # cloned should be Box, not Clonable
    print(cloned.value)  # 123 - accessing Box.value proves type is Box

    # Test 10: Builtin protocol with Self - Comparable.__lt__(Self)
    print(is_less(1, 2))  # True
    print(is_less(5, 3))  # False

main()
