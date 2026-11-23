# Error: a class at an `Own[X]` type argument conforms to a @dynamic protocol
# only as its declared base (BUGS.md#dynamic-adapter-own-type-arg-refused).
from typing import Protocol
from tpy import Own, dynamic, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@dynamic
class Sink(Protocol):
    def push(self, x: P) -> None: ...


class Box[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def push(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)


def use(s: Sink, p: P) -> None:
    s.push(p)


def main() -> None:
    b = Box[Own[P]]()
    use(b, P(1))  # tpyc: error(/Type Box\[Own\[P\]\] does not conform to protocol Sink in argument 's'/)


main()
