# Test Callable | None as parameter type with narrowing
from typing import Callable
from tpy import Int32

def maybe_apply(f: Callable[[Int32], Int32] | None, x: Int32) -> Int32:
    if f is not None:
        return f(x)
    return x

def make_doubler() -> Callable[[Int32], Int32]:
    return lambda x: x * 2

def main() -> None:
    doubler = make_doubler()
    print(maybe_apply(doubler, 5))
    print(maybe_apply(None, 5))

main()
