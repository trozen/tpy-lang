# A generator yielding a pointer-repr `Optional[record]`: the yield slot is
# a plain pointer, not the whole-optional shape the frame row admits, so
# `yield b` rejects.
from tpy import Int32
from typing import Iterator


class Box:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def g(b: Box | None) -> Iterator[Box | None]:  # tpyc: error(/res\.yield_type/)
    # The yield type is an optional record.
    yield b
    yield None


def main() -> None:
    pass


main()
