from tpy import int32
from typing import Callable
def add(x: int32) -> int32:
    return x + 1
def a(fs: list[Callable[[int32], int32]]) -> Callable[[int32], int32]:
    return fs[0]
def main() -> None:
    fs: list[Callable[[int32], int32]] = [add]
    print(a(fs)(1))
main()
