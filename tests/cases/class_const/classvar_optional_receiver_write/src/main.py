# Writing a mutable ClassVar through an Optional receiver: the deref check
# must run for AttributeError-on-None parity, but the write itself routes
# to class storage. Codegen emits `tpy::deref_check(c); ::Counter::n = v;`.
from typing import ClassVar, Optional
from tpy import Int32


class Counter:
    n: ClassVar[Int32] = 0

    def __init__(self) -> None:
        pass


def store(c: Optional[Counter], v: Int32) -> None:
    c.n = v  # tpyc: warning(/Potential None access/) warning(/Assigning to ClassVar 'Counter.n' via instance/)


def main() -> None:
    Counter.n = 0
    store(Counter(), 7)
    print(Counter.n)


main()
