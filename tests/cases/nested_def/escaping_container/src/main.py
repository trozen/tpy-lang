# Test escaping closure stored in a container via append (must capture by value)
from typing import Callable
from tpy import Int32

def main() -> None:
    callbacks: list[Callable[[Int32], Int32]] = []
    n: Int32 = 10
    def add_offset(x: Int32) -> Int32:
        return x + n
    callbacks.append(add_offset)
    f: Callable[[Int32], Int32] = callbacks[0]
    print(f(5))

main()
