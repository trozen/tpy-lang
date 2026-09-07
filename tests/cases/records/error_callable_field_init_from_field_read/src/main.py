# A callable member-init source must be a parameter NAME: a FIELD read off
# another record is a different emit shape and keeps rejecting.
from typing import Callable
from tpy import Int32


class Source:
    cb: Callable[[Int32], None]

    def __init__(self, cb: Callable[[Int32], None]) -> None:
        self.cb = cb


class Handler:
    cb: Callable[[Int32], None]

    def __init__(self, s: Source) -> None:
        self.cb = s.cb  # tpyc: error(/ctor.mil_field.callable.field/)


def show(n: Int32) -> None:
    print(n)


def main() -> None:
    h = Handler(Source(show))
    h.cb(3)


main()
