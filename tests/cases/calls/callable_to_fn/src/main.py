# Callable values passed to Fn parameters (implicit coercion)
from typing import Callable
from tpy import Fn, int32

def apply(f: Fn[[int32], int32], x: int32) -> int32:
    return f(x)

def double(x: int32) -> int32:
    return x * 2

class Handler:
    cb: Callable[[int32], int32]
    def __init__(self, cb: Callable[[int32], int32]) -> None:
        self.cb = cb

def main() -> None:
    # Callable variable passed to Fn param
    f: Callable[[int32], int32] = double
    print(apply(f, 21))

    # Callable variable with lambda
    g: Callable[[int32], int32] = lambda x: x + 100
    print(apply(g, 5))

    # Callable passed to builtin map (Fn param)
    h: Callable[[int32], int32] = double
    result = list(map(h, [1, 2, 3]))
    print(result)

    # Callable passed to builtin filter (Fn param)
    is_pos: Callable[[int32], bool] = lambda x: x > 0
    result2 = list(filter(is_pos, [-1, 2, -3, 4]))
    print(result2)

    # Callable field accessed and passed to Fn param
    handler = Handler(double)
    print(apply(handler.cb, 10))

main()
