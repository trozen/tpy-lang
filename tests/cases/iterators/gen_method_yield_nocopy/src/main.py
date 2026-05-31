# A generator METHOD re-yielding @nocopy elements of self.field is fine under
# Iterator[T]: the borrow ABI references the live element (no copy), so the
# deleted copy ctor is never invoked, and the loop var roots in the
# frame-resident self -- a durable borrow source. Mutation reaches self.items.
from typing import Iterator
from tpy import Int32, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32) -> None:
        self.fd = fd


class Box:
    items: list[Handle]

    def __init__(self) -> None:
        self.items = [Handle(1), Handle(2)]

    def each(self) -> Iterator[Handle]:  # tpyc: ok
        for h in self.items:
            yield h


def main() -> None:
    b = Box()
    for h in b.each():
        h.fd = h.fd + 10
    for h in b.items:
        print(h.fd)


main()
