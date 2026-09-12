# A BORROW-returning method at a member-init field slot is not an rvalue source,
# so the direct-construct row must not claim it. Concretely, `self.mine =
# s.peek()` in `Holder.__init__` initializes a field from a borrow-returning
# method; TPy rejects that member init today.
from tpy import int32, Own


class Val:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Source:
    v: Val

    def __init__(self, v: Own[Val]) -> None:
        self.v = v

    def peek(self) -> Val:
        return self.v


class Holder:
    mine: Val

    def __init__(self, s: Source) -> None:
        self.mine = s.peek()  # tpyc: error(/ctor.mil_field.record.method/)


def main() -> None:
    h = Holder(Source(Val(1)))
    print(h.mine.x)


main()
