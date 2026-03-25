# Test escaping closure stored in a class field (must capture by value)
from typing import Callable
from tpy import Int32

class Handler:
    callback: Callable[[Int32], Int32]

    def __init__(self, n: Int32) -> None:
        def add_offset(x: Int32) -> Int32:
            return x + n
        self.callback = add_offset

def main() -> None:
    h = Handler(10)
    print(h.callback(5))
    h2 = Handler(100)
    print(h2.callback(42))

main()
