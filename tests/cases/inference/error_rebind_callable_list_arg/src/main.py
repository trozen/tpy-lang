# A float local rebound to call0([lambda: 1]) is refused: the lambda keeps its
# int return under the seeded Callable[[], float] element hint.
from typing import Callable

from tpy import Own


def call0[T](fs: list[Callable[[], T]]) -> Own[T]:
    return fs[0]()


def rebind() -> None:
    y = 0.5
    y = call0([lambda: 1])  # tpyc: error(/'y' is bound to float at line 13 and to int32 here/)
    print(y)


rebind()
