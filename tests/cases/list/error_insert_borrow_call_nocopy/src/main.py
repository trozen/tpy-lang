# A @nocopy payload at the same element slot is the located error, not a
# warning: the copy the slot would make is impossible, so the diagnostic the
# copyable payload gets as a warning is an error here.
from tpy import int32, nocopy


@nocopy
class Res:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    r: Res

    def __init__(self) -> None:
        self.r = Res(7)

    def borrow(self) -> Res:
        return self.r


def main() -> None:
    h = Holder()
    xs: list[Res] = []
    xs.append(h.borrow())  # tpyc: error(/cannot copy non-copyable type 'Res' into owned storage/)
    print(xs[0].v)


main()
