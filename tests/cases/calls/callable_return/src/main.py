# Test Callable as function return type (captures by value)
from typing import Callable
from tpy import Int32

def make_adder(n: Int32) -> Callable[[Int32], Int32]:
    return lambda x: x + n

def make_multiplier(factor: Int32) -> Callable[[Int32], Int32]:
    return lambda x: x * factor

def main() -> None:
    add5 = make_adder(5)
    print(add5(10))  # 15
    print(add5(0))   # 5

    mul3 = make_multiplier(3)
    print(mul3(7))   # 21

main()
