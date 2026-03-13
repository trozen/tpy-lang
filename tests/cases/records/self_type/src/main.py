# Self type in record methods: builder pattern, method chaining, generic classes, Optional[Self]
from typing import Self, Optional
from tpy import Int32, readonly, auto_readonly

class Builder:
    name: str
    value: Int32

    def __init__(self, name: str, value: Int32) -> None:
        self.name = name
        self.value = value

    def set_name(self, name: str) -> Self:
        self.name = name
        return self

    def set_value(self, value: Int32) -> Self:
        self.value = value
        return self

    def with_offset(self, other: Self) -> Int32:
        return self.value + other.value

    @auto_readonly
    def find_match(self, target: Int32) -> Optional[Self]:
        if self.value == target:
            return self
        return None

def test_builder() -> None:
    b = Builder("start", Int32(0))
    # Method chaining via Self return type
    b.set_name("hello").set_value(Int32(42))
    print(b.name)
    print(b.value)

    # Self in parameter type
    b2 = Builder("other", Int32(10))
    print(b.with_offset(b2))

    # Optional[Self] return
    result = b.find_match(Int32(42))
    if result is not None:
        print(result.name)
    result2 = b.find_match(Int32(99))
    if result2 is None:
        print("not found")

class Stack[T]:
    items: list[T]
    label: str

    def __init__(self, label: str) -> None:
        self.items = []
        self.label = label

    def push(self, item: T) -> Self:
        self.items.append(item)
        return self

    @readonly
    def describe(self) -> str:
        return self.label

def test_generic() -> None:
    s = Stack[Int32]("my_stack")
    # Method chaining on generic class
    s.push(Int32(10)).push(Int32(20)).push(Int32(30))
    print(len(s.items))
    print(s.describe())

test_builder()
test_generic()
