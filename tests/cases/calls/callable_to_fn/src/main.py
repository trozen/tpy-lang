# Callable values passed to Fn parameters (implicit coercion)
from typing import Callable
from tpy import Fn, Int32

def apply(f: Fn[[Int32], Int32], x: Int32) -> Int32:
    return f(x)

def double(x: Int32) -> Int32:
    return x * 2

class Handler:
    cb: Callable[[Int32], Int32]
    def __init__(self, cb: Callable[[Int32], Int32]) -> None:
        self.cb = cb

def main() -> None:
    # Callable variable passed to Fn param
    f: Callable[[Int32], Int32] = double
    print(apply(f, 21))

    # Callable variable with lambda
    g: Callable[[Int32], Int32] = lambda x: x + 100
    print(apply(g, 5))

    # Callable passed to builtin map (Fn param)
    h: Callable[[Int32], Int32] = double
    result = list(map(h, [1, 2, 3]))
    print(result)

    # Callable passed to builtin filter (Fn param)
    is_pos: Callable[[Int32], bool] = lambda x: x > 0
    result2 = list(filter(is_pos, [-1, 2, -3, 4]))
    print(result2)

    # Callable field accessed and passed to Fn param
    handler = Handler(double)
    print(apply(handler.cb, 10))

main()
