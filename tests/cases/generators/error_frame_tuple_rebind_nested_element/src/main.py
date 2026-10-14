# A nested tuple element read off a generator's rebound tuple is refused, as
# in a plain function body: no row reads an element of a pointer tuple's
# nested tuple yet (BUGS.md#nested-storage-tuple-element-read).
from typing import Iterator

from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# the reject is located at the frame's def, not at the read inside it
def run() -> Iterator[int32]:  # tpyc: error(/subscript\.tuple_shape/)
    p = ((Box(1), Box(2)), Box(3))
    yield p[0][1].n
    p = ((Box(9), Box(8)), Box(7))
    yield p[0][1].n


def main() -> None:
    print(list(run()))


main()
