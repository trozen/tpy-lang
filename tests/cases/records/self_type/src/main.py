# Self type in record methods: builder pattern, method chaining, generic classes, Optional[Self]
from typing import Self, Optional
from tpy import int32, readonly, auto_readonly

class Builder:
    name: str
    value: int32

    def __init__(self, name: str, value: int32) -> None:
        self.name = name
        self.value = value

    def set_name(self, name: str) -> Self:
        self.name = name
        return self

    def set_value(self, value: int32) -> Self:
        self.value = value
        return self

    def with_offset(self, other: Self) -> int32:
        return self.value + other.value

    @auto_readonly
    def find_match(self, target: int32) -> Optional[Self]:
        if self.value == target:
            return self
        return None

def test_builder() -> None:
    b = Builder("start", int32(0))
    # Method chaining via Self return type
    b.set_name("hello").set_value(int32(42))
    print(b.name)
    print(b.value)

    # Self in parameter type
    b2 = Builder("other", int32(10))
    print(b.with_offset(b2))

    # Optional[Self] return
    result = b.find_match(int32(42))
    if result is not None:
        print(result.name)
    result2 = b.find_match(int32(99))
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
    s = Stack[int32]("my_stack")
    # Method chaining on generic class
    s.push(int32(10)).push(int32(20)).push(int32(30))
    print(len(s.items))
    print(s.describe())

test_builder()
test_generic()
