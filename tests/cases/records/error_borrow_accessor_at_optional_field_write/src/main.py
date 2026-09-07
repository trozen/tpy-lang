# A `T&`-returning accessor result at an Optional field write is a BORROW, not
# the owned-rvalue slice the write row admits. Concretely, `n.byval =
# h.get()` writes a borrowed accessor result into an optional field; TPy
# rejects that assignment today.
from tpy import Int32, copy


class Val:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Holder:
    v: Val
    byval: Val | None

    def __init__(self, v: Val) -> None:
        self.v = copy(v)
        self.byval = None

    def get(self) -> Val:
        return self.v


def use(h: Holder, n: Holder) -> None:
    n.byval = h.get()  # tpyc: error(/assign.field_write_shape/)


def main() -> None:
    use(Holder(Val(1)), Holder(Val(2)))


main()
