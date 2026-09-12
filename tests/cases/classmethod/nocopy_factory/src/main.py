# A @nocopy record built through a classmethod: `cls(...)` -> Own[Self] must
# move, never copy. A silent copy at the factory boundary would be a compile
# error on a @nocopy type, so this case pins the ownership transfer.
from typing import Self

from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd

    @classmethod
    def opened(cls, fd: int32) -> Own[Self]:
        return cls(fd)


def consume(h: Own[Handle]) -> int32:
    return h.fd


def main() -> None:
    h = Handle.opened(7)
    print(h.fd)
    print(consume(h))


main()
