# Narrowed value-Optional FIELDS at generator sinks deref to the inner
# value when no suspension intervenes since the guard (sema kills
# field-path narrow facts at yield/await, so a surviving fact is sound).
# Faces: field yield, frame reassign from field, re-guard after a yield
# (re-checked at resume -- observed via mutation between next() calls),
# local-bind escape carrying the fact across suspensions.
from tpy import Int32
from typing import Iterator


class Box:
    f: Int32 | None

    def __init__(self, v: Int32 | None) -> None:
        self.f = v

    def gen_field(self) -> Iterator[Int32]:
        if self.f is not None:
            yield self.f
        yield -1

    def gen_reassign(self) -> Iterator[Int32]:
        q = 0
        if self.f is not None:
            q = self.f
        yield q
        yield -2

    def gen_reguard(self) -> Iterator[Int32]:
        if self.f is not None:
            yield self.f
        if self.f is not None:
            yield self.f + 1
        yield -3

    def gen_local_bind(self) -> Iterator[Int32]:
        v = self.f
        if v is not None:
            yield v
            yield v + 1
        yield -4


def main() -> None:
    b = Box(4)
    for x in b.gen_field():
        print(x)
    for x in b.gen_reassign():
        print(x)
    for x in b.gen_local_bind():
        print(x)
    # The second guard in gen_reguard re-checks the field at resume:
    # clearing it between next() calls skips the narrowed yield.
    it = b.gen_reguard()
    try:
        print(next(it))
        b.f = None
        print(next(it))
    except StopIteration:
        print("stop")
    n = Box(None)
    for x in n.gen_field():
        print(x)
    for x in n.gen_reassign():
        print(x)


main()
