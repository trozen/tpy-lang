# The inverse of ctor_comprehension_arg: a constructor call with a
# comprehension argument as a RECORD FIELD assignment has no statement flush
# for the hoisted temp and keeps rejecting
# (BUGS.md#ctor-call-arg-temp-flush-positions).
from tpy import Int32


class Flat:
    n: Int32

    def __init__(self, data: list[bytes]) -> None:
        self.n = len(data)


class Holder:
    f: Flat

    def __init__(self) -> None:
        self.f = Flat([b"a"])


def main() -> None:
    h = Holder()
    h.f = Flat([bytes([i]) for i in range(5)])  # tpyc: error(/call.ctor_arg.container/)
    print(h.f.n)


main()
