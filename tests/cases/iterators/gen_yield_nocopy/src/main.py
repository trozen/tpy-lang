# A @nocopy element is fine under Iterator[T]: the declaration-driven borrow ABI
# hands out a reference (no copy), so the deleted copy ctor is never invoked.
# Consumer mutation reaches the source elements -- CPython reference semantics.
from typing import Iterator
from tpy import int32, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32) -> None:
        self.fd = fd


def handles(items: list[Handle]) -> Iterator[Handle]:  # tpyc: ok
    for h in items:
        yield h


def main() -> None:
    data = [Handle(1), Handle(2)]
    for h in handles(data):
        h.fd = h.fd + 10
    for h in data:
        print(h.fd)


main()
