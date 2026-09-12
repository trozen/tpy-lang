# Test escaping closure stored in a class field (must capture by value)
from typing import Callable
from tpy import int32

class Handler:
    callback: Callable[[int32], int32]

    def __init__(self, n: int32) -> None:
        def add_offset(x: int32) -> int32:
            return x + n
        self.callback = add_offset

def main() -> None:
    h = Handler(10)
    print(h.callback(5))
    h2 = Handler(100)
    print(h2.callback(42))

main()
