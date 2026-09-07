# A SELF-capturing lambda as a callable member-init source: the `this` receiver
# spelling is not part of the routed slice.
from typing import Callable
from tpy import Int32


class Handler:
    n: Int32
    action: Callable[[], None]

    def __init__(self) -> None:
        self.n = 1
        self.action = lambda: print(self.n)  # tpyc: error(/ctor.mil_field.callable.lambda/)


def main() -> None:
    h = Handler()
    h.action()


main()
