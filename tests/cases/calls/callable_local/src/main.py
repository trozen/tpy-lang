# Test Callable as local variable type annotation
from typing import Callable
from tpy import int32

def main() -> None:
    f: Callable[[int32], int32] = lambda x: x + 1
    print(f(10))  # 11

    g: Callable[[int32, int32], int32] = lambda a, b: a * b
    print(g(3, 4))  # 12

    h: Callable[[], str] = lambda: "hello"
    print(h())  # hello

main()
