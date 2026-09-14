# A pointer-form frame local rebound while nothing else holds its payload
# writes in place: the superseded payload drops at the rebind, as under
# CPython, instead of living in a second per-site field until the frame dies.
from typing import Iterator, Optional
from tpy import int32


class Resource:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __del__(self):
        print("drop", self.n)


def gen() -> Iterator[int32]:
    # Two write sites, nothing aliases the first payload: the second write
    # assigns through the pointer and drops Resource(1) right there.
    saved: Optional[Resource] = Resource(1)
    yield 1
    saved = Resource(2)
    yield 2


def main() -> None:
    for v in gen():
        print(v)
    print("done")


main()
