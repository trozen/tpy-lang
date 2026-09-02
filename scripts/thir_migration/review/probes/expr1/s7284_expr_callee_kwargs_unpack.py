from typing import Callable
from tpy import Int32
def mk(n: Int32) -> Callable[[Int32], Int32]:
    def add(x: Int32) -> Int32:
        return x + n
    return add
def f(d: dict[str, Int32]) -> Int32:
    return mk(10)(5, **d)
def main() -> None:
    pass
main()
