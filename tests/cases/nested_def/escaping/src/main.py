# Test escaping nested def returned as Callable (factory pattern)
from typing import Callable
from tpy import Int32

def make_adder(n: Int32) -> Callable[[Int32], Int32]:
    def add(x: Int32) -> Int32:
        return x + n
    return add

def main() -> None:
    add5 = make_adder(5)
    print(add5(10))
    add100 = make_adder(100)
    print(add100(42))

main()
