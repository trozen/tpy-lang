from tpy import Int32
from typing import Callable
def run(f: Callable[[Int32], None], x: Int32) -> None:
    f(x)
def a1() -> None:
    run(lambda x: print(x), 1)
def a2(n: Int32 | None) -> None:
    if n is not None:
        run(lambda x: print(x, n), 1)
def a3(u: Int32 | str) -> None:
    if isinstance(u, Int32):
        run(lambda x: print(x, u), 1)
a1(); a2(3); a3(4)
