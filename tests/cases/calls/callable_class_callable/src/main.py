# Pass callable objects to Callable parameters (std::function wrapping)
from tpy import int32
from typing import Callable

class Doubler:
    def __call__(self, x: int32) -> int32:
        return x * 2

class Adder:
    offset: int32
    def __init__(self, offset: int32):
        self.offset = offset
    def __call__(self, x: int32) -> int32:
        return x + self.offset

def apply(f: Callable[[int32], int32], x: int32) -> int32:
    return f(x)

def main():
    d = Doubler()
    print(apply(d, 5))

    a = Adder(100)
    print(apply(a, 5))

main()
