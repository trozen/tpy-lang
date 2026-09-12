# Writes into a value-storage Optional[record] field: an Own[Inner] NAME moved
# in at last use, and a record RVALUE. @nocopy Inner makes a silent copy a
# compile error, so the move is enforced; get() reads the moved-in value back.
from tpy import int32, Own, nocopy


@nocopy
class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    opt: Inner | None

    def __init__(self) -> None:
        self.opt = None

    def set_name(self, p: Own[Inner]) -> None:
        self.opt = p

    def set_rvalue(self, v: int32) -> None:
        self.opt = Inner(v)

    def get(self) -> int32:
        if self.opt is not None:
            return self.opt.v
        return -1


def main() -> None:
    h = Holder()
    print(h.get())
    h.set_name(Inner(10))
    print(h.get())
    h.set_rvalue(20)
    print(h.get())


main()
