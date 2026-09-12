# Pins the per-site frame-slot drop order: a superseded rvalue payload
# lives in its own site's slot until the frame dies (CPython drops it at
# the rebind -- the declared divergence in no_cpython.txt).
from typing import Iterator, Optional
from tpy import int32


class Resource:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __del__(self):
        print("drop", self.n)


def gen() -> Iterator[int32]:
    # Two distinct write sites: Resource(1)'s slot is never revisited, so
    # its payload drops only at frame destruction, after Resource(2)'s.
    saved: Optional[Resource] = Resource(1)
    yield 1
    saved = Resource(2)
    yield 2


def main() -> None:
    for v in gen():
        print(v)
    print("done")


main()
