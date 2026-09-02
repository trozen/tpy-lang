from typing import Callable
from tpy import Int32
def f(x: Int32) -> Int32:
    return x + 1
def g(f: Callable[[Int32], Int32], y: Int32) -> Int32:
    return f(y)
def main() -> None:
    pass
main()
