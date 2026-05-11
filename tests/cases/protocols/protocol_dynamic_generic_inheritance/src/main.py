# Generic @dynamic protocol extending another generic @dynamic protocol.
# Child[T] : Parent[T] base-class chain in C++; the second half of the
# test exercises the implicit upcast from Counter[Int32] to Source[Int32]
# (child-protocol value used where the parent is expected).
from typing import Protocol
from tpy import Int32, dynamic


@dynamic
class Source[T](Protocol):
    def get(self) -> T:
        ...


@dynamic
class Counter[T](Source[T], Protocol):
    def bump(self) -> None:
        ...


class IntCounter:
    n: Int32

    def __init__(self):
        self.n = 0

    def get(self) -> Int32:
        return self.n

    def bump(self) -> None:
        self.n = self.n + 1


def show_source(s: Source[Int32]) -> None:
    print(s.get())


def main() -> None:
    c: Counter[Int32] = IntCounter()
    c.bump()
    c.bump()
    c.bump()
    print(c.get())
    show_source(c)  # Counter[Int32] -> Source[Int32] via implicit upcast
    s: Source[Int32] = IntCounter()
    print(s.get())


main()
