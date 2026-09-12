from typing import Callable
from tpy import int32
def f(x: int32) -> int32:
    return x + 1
def g(f: Callable[[int32], int32], y: int32) -> int32:
    return f(y)
def main() -> None:
    pass
main()
