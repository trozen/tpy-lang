# A float local rebound to a generic call whose T only the local would pick
# for a lambda argument is refused: apply(lambda: 1) returns the int 1.
from typing import Callable


def apply[T](fn: Callable[[], T]) -> T:
    return fn()


def rebind() -> None:
    y = 0.5
    y = apply(lambda: 1)  # tpyc: error(/'y' is bound to float at line 11 and to int32 here/)
    print(y)


rebind()
