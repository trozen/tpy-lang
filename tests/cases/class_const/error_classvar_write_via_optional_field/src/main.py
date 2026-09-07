# A ClassVar written through an unproven-Optional FIELD receiver: the receiver
# needs its own None check, so it is outside the bare qualified class-constant
# lvalue the write arm renders, so `h.c.n = 5` rejects.
from typing import ClassVar
from tpy import Int32


class C:
    n: ClassVar[Int32] = 0

    def __init__(self) -> None:
        pass


class H:
    c: C | None

    def __init__(self) -> None:
        self.c = C()


def store(h: H) -> None:
    h.c.n = 5  # tpyc: error(/assign\.field_write_shape/)


def main() -> None:
    store(H())
    print(C.n)


main()
