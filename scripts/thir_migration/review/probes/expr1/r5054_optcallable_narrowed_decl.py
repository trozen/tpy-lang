from typing import Callable
from tpy import int32
def f(cb: Callable[[int32], int32] | None) -> int32:
    if cb is not None:
        h = cb
        return h(1)
    return 0
def main() -> None:
    pass
main()
