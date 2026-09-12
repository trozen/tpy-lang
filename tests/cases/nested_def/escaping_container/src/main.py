# Test escaping closure stored in a container via append (must capture by value)
from typing import Callable
from tpy import int32

def main() -> None:
    callbacks: list[Callable[[int32], int32]] = []
    n: int32 = 10
    def add_offset(x: int32) -> int32:
        return x + n
    callbacks.append(add_offset)
    f: Callable[[int32], int32] = callbacks[0]
    print(f(5))

main()
