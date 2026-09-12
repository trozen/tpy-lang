# Generic static method calls: type inference and explicit type args
from tpy import *

class Container[T]:
    value: T

    def __init__(self, value: Own[T]):
        self.value = value

    def __repr__(self) -> str:
        return f"Container(value={self.value!r})"

    @staticmethod
    def create(v: Own[T]) -> Own[Container[T]]:
        return Container(v)

    @staticmethod
    def wrap_optional(v: Own[T] | None) -> Own[Container[T]] | None:
        if v is not None:
            return Container(v)
        return None

def main() -> None:
    # Inference from argument type
    c1 = Container.create(42)
    print("c1:", c1)

    # Explicit type args
    c2 = Container[int32].create(10)
    print("c2:", c2)

    # Inference with optional param (non-None value)
    c3 = Container.wrap_optional(99)
    print("c3:", c3)

    # Explicit with None
    c4 = Container[int32].wrap_optional(None)
    print("c4:", c4)

    # Explicit with value
    c5 = Container[int32].wrap_optional(77)
    print("c5:", c5)

main()
