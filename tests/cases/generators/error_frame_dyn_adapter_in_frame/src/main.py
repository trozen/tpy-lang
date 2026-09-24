# In a generator body, a generator given a value for a @dynamic protocol
# parameter would outlive that value's protocol view (BUGS.md#res-param-dyn-protocol-frame).
from typing import Iterator, Protocol

from tpy import int32, dynamic


@dynamic
class Getter(Protocol):
    def get(self) -> int32: ...


class Val:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def get(self) -> int32:
        return self.v


def from_getter(s: Getter) -> Iterator[int32]:
    yield s.get()
    yield s.get() + 1


def outer(b: Val) -> Iterator[int32]:
    # The subject: the Getter view of b lasts only until the next yield.
    for w in from_getter(b):  # tpyc: error(/cannot pass this value to @dynamic protocol parameter 's' of generator 'from_getter'/)
        yield w


print(list(outer(Val(5))))
