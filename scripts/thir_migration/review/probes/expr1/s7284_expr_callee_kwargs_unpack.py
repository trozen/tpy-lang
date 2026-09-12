from typing import Callable
from tpy import int32
def mk(n: int32) -> Callable[[int32], int32]:
    def add(x: int32) -> int32:
        return x + n
    return add
def f(d: dict[str, int32]) -> int32:
    return mk(10)(5, **d)
def main() -> None:
    pass
main()
