# A generator yielding a pointer-repr `Optional[record]` parameter: the
# yield slot holds the optional's storage, so the yield would copy the
# caller's record, a copy sema did not declare -- it is refused.
from tpy import int32
from typing import Iterator


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def g(b: Box | None) -> Iterator[Box | None]:  # tpyc: error(/return\.copy_undeclared/)
    # The yield type is an optional record.
    yield b
    yield None


def main() -> None:
    pass


main()
