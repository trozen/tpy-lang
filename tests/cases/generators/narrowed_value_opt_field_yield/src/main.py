# Narrowed value-Optional FIELDS at generator sinks deref to the inner
# value when no suspension intervenes since the guard (sema kills
# field-path narrow facts at yield/await, so a surviving fact is sound).
# Faces: field yield, frame reassign from field, re-guard after a yield
# (re-checked at resume -- observed via mutation between next() calls),
# local-bind escape carrying the fact across suspensions, the first yield
# of a try body, a local-bind yield under a loop back-edge.
from tpy import int32
from typing import Iterator


class Box:
    f: int32 | None

    def __init__(self, v: int32 | None) -> None:
        self.f = v

    def gen_field(self) -> Iterator[int32]:
        if self.f is not None:
            yield self.f
        yield -1

    def gen_reassign(self) -> Iterator[int32]:
        q = 0
        if self.f is not None:
            q = self.f
        yield q
        yield -2

    def gen_reguard(self) -> Iterator[int32]:
        if self.f is not None:
            yield self.f
        if self.f is not None:
            yield self.f + 1
        yield -3

    def gen_local_bind(self) -> Iterator[int32]:
        v = self.f
        if v is not None:
            yield v
            yield v + 1
        yield -4

    def gen_try_body(self) -> Iterator[int32]:
        # The guard survives into the try body, so its FIRST yield still
        # derefs; only handler entry kills the fact (the reject is
        # error_narrowed_field_stale_except).
        if self.f is not None:
            try:
                yield self.f
                raise ValueError("boom")
            except ValueError:
                yield -5
        yield -6

    def gen_local_bind_loop(self) -> Iterator[int32]:
        # A local binding carries the fact across a loop back-edge that
        # crosses the yield; reading the field there instead is the reject
        # error_narrowed_field_stale_loop_yield pins.
        v = self.f
        if v is not None:
            for _i in range(2):
                yield v
        yield -7


def main() -> None:
    b = Box(4)
    for x in b.gen_field():
        print(x)
    for x in b.gen_reassign():
        print(x)
    for x in b.gen_local_bind():
        print(x)
    for x in b.gen_try_body():
        print("try_body", x)
    for x in b.gen_local_bind_loop():
        print("bind_loop", x)
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
