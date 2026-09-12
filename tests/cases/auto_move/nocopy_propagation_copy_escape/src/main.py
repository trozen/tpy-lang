# __copy__ escape hatch: class with nocopy field remains copyable via user-defined copy
from __future__ import annotations
from tpy import int32, Own, nocopy, copy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


class Container:
    handle: Handle

    def __init__(self, handle: Own[Handle]):
        self.handle = handle

    def __copy__(self) -> Own[Container]:
        return Container(Handle(self.handle.fd))


def consume(c: Own[Container]) -> int32:
    return c.handle.fd


def main():
    c = Container(Handle(42))
    c2 = copy(c)
    print(consume(c))
    print(consume(c2))


main()
