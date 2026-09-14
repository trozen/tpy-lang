# A generator yielding an OPTIONAL container lowers; the consumer's for-loop
# over its `Optional[list]` elements is the shape that still rejects (the
# optional element family has no foreach arm).
from typing import Iterator, Optional
from tpy import int32


def maybe(xs: list[int32], n: int32) -> Iterator[Optional[list[int32]]]:
    i = 0
    while i < n:
        yield xs
        i += 1


def main() -> None:
    for got in maybe([1], 1):  # tpyc: error(/foreach.elem_family.optional/)
        if got is not None:
            print(len(got))


main()
