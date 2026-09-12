from tpy import int32
from typing import Callable
def run(f: Callable[[int32], None], x: int32) -> None:
    f(x)
def a1() -> None:
    run(lambda x: print(x), 1)
def a2(n: int32 | None) -> None:
    if n is not None:
        run(lambda x: print(x, n), 1)
def a3(u: int32 | str) -> None:
    if isinstance(u, int32):
        run(lambda x: print(x, u), 1)
a1(); a2(3); a3(4)
