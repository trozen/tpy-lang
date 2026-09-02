from typing import Callable
from tpy import Int32
def f(cb: Callable[[Int32], Int32] | None) -> Int32:
    if cb is not None:
        h = cb
        return h(1)
    return 0
def main() -> None:
    pass
main()
