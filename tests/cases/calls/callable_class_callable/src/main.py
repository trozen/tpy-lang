# Pass callable objects to Callable parameters (std::function wrapping)
from tpy import Int32
from typing import Callable

class Doubler:
    def __call__(self, x: Int32) -> Int32:
        return x * 2

class Adder:
    offset: Int32
    def __init__(self, offset: Int32):
        self.offset = offset
    def __call__(self, x: Int32) -> Int32:
        return x + self.offset

def apply(f: Callable[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def main():
    d = Doubler()
    print(apply(d, 5))

    a = Adder(100)
    print(apply(a, 5))

main()
