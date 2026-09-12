# Test named function references with Callable (type-erased)
from typing import Callable
from tpy import int32

def double(x: int32) -> int32:
    return x * 2

def greet(name: str) -> str:
    return "Hello, " + name

def apply(f: Callable[[int32], int32], x: int32) -> int32:
    return f(x)

class Handler:
    callback: Callable[[int32], None]

    def __init__(self, cb: Callable[[int32], None]) -> None:
        self.callback = cb

    def run(self, x: int32) -> None:
        self.callback(x)

    def __str__(self) -> str:
        return "Handler(...)"

def printer(x: int32) -> None:
    print("got:", x)

def main() -> None:
    # Callable param
    print(apply(double, 21))  # 42

    # Callable local
    f: Callable[[int32], int32] = double
    print(f(10))  # 20

    # Callable field via constructor
    h = Handler(printer)
    h.run(7)  # got: 7

    # Callable with string types
    g: Callable[[str], str] = greet
    print(g("World"))  # Hello, World

main()
