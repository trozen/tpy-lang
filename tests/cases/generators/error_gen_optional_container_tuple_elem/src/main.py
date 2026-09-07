# A tuple element typed `Optional[list[...]]` unpacked in a generator loop:
# the container flavour of an optional is outside the optional-ptr borrow.
from typing import Iterator, Optional
from tpy import Int32


def gen(pairs: list[tuple[Optional[list[Int32]], Int32]]) -> Iterator[Int32]:  # tpyc: error(/res\.local_storage/)
    # `xs` is a container-optional unpack target living in the frame.
    for xs, n in pairs:
        if xs is not None:
            yield len(xs)
        yield n


def main() -> None:
    for v in gen([([1, 2], 3)]):
        print(v)


main()
