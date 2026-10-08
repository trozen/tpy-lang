# A reseated record local (a `P*` the frame rebinds) written into an
# `Optional[P]` field: the pointer binding lands in the whole optional and
# has no lift there yet (BUGS.md#reseated-record-local-into-optional-field).
from typing import Optional


class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class H:
    o: Optional[P]

    def __init__(self) -> None:
        self.o = None

    def pick(self, a: P, b: P, c: bool) -> None:
        p = a
        if c:
            p = b
        self.o = p  # tpyc: warning(/copies P into field/) error(/field_write.lift.borrow/)


def main() -> None:
    h = H()
    a = P(1)
    b = P(2)
    h.pick(a, b, True)


main()
