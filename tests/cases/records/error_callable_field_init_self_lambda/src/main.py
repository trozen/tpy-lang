# A lambda capturing `self`, stored into a field in the constructor: a copy or
# move of the object would leave the closure pointing at the original.
from typing import Callable
from tpy import int32


class Handler:
    n: int32
    action: Callable[[], None]

    def __init__(self) -> None:
        self.n = 1
        self.action = lambda: print(self.n)  # tpyc: error(/A lambda stored in field 'action' cannot capture 'self'/)


def main() -> None:
    h = Handler()
    h.action()


main()
