from tpy import Int32
from typing import Callable
def add(x: Int32) -> Int32:
    return x + 1
def a(fs: list[Callable[[Int32], Int32]]) -> Callable[[Int32], Int32]:
    return fs[0]
def main() -> None:
    fs: list[Callable[[Int32], Int32]] = [add]
    print(a(fs)(1))
main()
