# A pointer-form frame local rebound while nothing else holds its payload
# takes a per-site frame field of its own: each payload drops exactly once,
# the superseded one when the frame dies (CPython drops it at the rebind, a
# timing Python does not promise), the current one at an explicit `del`.
from typing import Iterator, Optional
from tpy import int32


DROPPED: list[int32] = []


class Resource:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __del__(self):
        DROPPED.append(self.n)


def gen() -> Iterator[int32]:
    saved: Optional[Resource] = Resource(1)
    yield 1
    # The rebind's new payload lands in a field of its own.
    saved = Resource(2)
    yield 2
    # The defined drop point of the current payload, in both runs.
    del saved
    yield 3


def run() -> None:
    for v in gen():
        print(v, 2 in DROPPED)


def main() -> None:
    run()
    print("dropped", sorted(DROPPED))
    print("done")


main()
