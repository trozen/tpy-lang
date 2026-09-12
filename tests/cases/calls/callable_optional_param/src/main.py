# Test Callable | None as parameter type with narrowing
from typing import Callable
from tpy import int32

def maybe_apply(f: Callable[[int32], int32] | None, x: int32) -> int32:
    if f is not None:
        return f(x)
    return x

def make_doubler() -> Callable[[int32], int32]:
    return lambda x: x * 2

def main() -> None:
    doubler = make_doubler()
    print(maybe_apply(doubler, 5))
    print(maybe_apply(None, 5))

main()
