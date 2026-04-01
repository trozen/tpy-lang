# Literal overload flattening: per-literal C++ specializations with different
# return types. Each Literal stub gets its own mangled C++ function/method.
from typing import Literal, overload
from tpy import Int32


# --- Free function flattening ---

@overload
def get_field(name: Literal["age"]) -> Int32: ...

@overload
def get_field(name: Literal["name"]) -> str: ...

@overload
def get_field(name: str) -> Int32 | str: ...

def get_field(name: str) -> Int32 | str:
    if name == "age":
        return 42
    return "hello"


# --- Method flattening ---

class Record:
    data_age: Int32
    data_name: str

    def __init__(self, age: Int32, name: str) -> None:
        self.data_age = age
        self.data_name = name

    @overload
    def get(self, key: Literal["age"]) -> Int32: ...

    @overload
    def get(self, key: Literal["name"]) -> str: ...

    @overload
    def get(self, key: str) -> Int32 | str: ...

    def get(self, key: str) -> Int32 | str:
        if key == "age":
            return self.data_age
        return self.data_name


def main() -> None:
    # Free function dispatch
    age = get_field("age")
    name = get_field("name")
    print(age + 1)
    print(name + "!")

    # Method dispatch
    r = Record(25, "Alice")
    a = r.get("age")
    n = r.get("name")
    print(a + 1)
    print(n + "!")


main()
