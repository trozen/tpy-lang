# Nested call inference: parameter type flows as hint to inner call.
from tpy import Int32, Own

class Box[T]:
    val: T

    def __init__(self, val: Own[T]) -> None:
        self.val = val

def wrap[T](v: T) -> Own[Box[T]]:
    return Box[T](v)

def sink(b: Own[Box[Int32]]) -> None:
    print(b.val)

def take_two(a: Own[Box[Int32]], b: Own[Box[Int32]]) -> None:
    print(a.val + b.val)

def main() -> None:
    # T=Int32 inferred from sink's parameter type
    sink(wrap(Int32(10)))
    take_two(wrap(Int32(3)), wrap(Int32(7)))
    # Bare literals: hint chain infers Int32, coerces literal
    sink(wrap(20))
    take_two(wrap(5), wrap(9))
    print("done")

main()
