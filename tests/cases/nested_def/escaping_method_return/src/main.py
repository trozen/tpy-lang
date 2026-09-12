# Test escaping closure returned from a method (must capture by value)
from typing import Callable
from tpy import int32

class Factory:
    def make_adder(self, n: int32) -> Callable[[int32], int32]:
        def add(x: int32) -> int32:
            return x + n
        return add

def main() -> None:
    f = Factory()
    add5 = f.make_adder(5)
    print(add5(10))
    add100 = f.make_adder(100)
    print(add100(42))

main()
