# Nested call inference: parameter type flows as hint to inner call.
from tpy import int32, Own

class Box[T]:
    val: T

    def __init__(self, val: Own[T]) -> None:
        self.val = val

def wrap[T](v: T) -> Own[Box[T]]:
    return Box[T](v)

def sink(b: Own[Box[int32]]) -> None:
    print(b.val)

def take_two(a: Own[Box[int32]], b: Own[Box[int32]]) -> None:
    print(a.val + b.val)

def main() -> None:
    # T=int32 inferred from sink's parameter type
    sink(wrap(int32(10)))
    take_two(wrap(int32(3)), wrap(int32(7)))
    # Bare literals: hint chain infers int32, coerces literal
    sink(wrap(20))
    take_two(wrap(5), wrap(9))
    print("done")

main()
