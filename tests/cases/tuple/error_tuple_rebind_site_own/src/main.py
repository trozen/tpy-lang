# A rebind of a mixed tuple local inside a generator body while a name still
# refers to the object its owned element was bound to: a frame has no
# per-element storage yet, so the rebind is refused, naming the alias, where
# writing in place would clobber it
# (BUGS.md#frame-tuple-rebind-own-site-rejects).
from typing import Iterator

from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def rebind(v: Box, w: Box) -> Iterator[int32]:
    p = make_mixed(1, v)
    u = p
    yield u[0].n
    p = make_mixed(9, w)  # tpyc: error(/'u' still refers to the object an element of 'p' was bound to, so the object the rebind of 'p' creates needs storage of its own/)
    u[0].n += 100
    yield u[0].n


for n in rebind(Box(10), Box(20)):
    print(n)
