# The element slot takes the same one rule as the Own return: a
# borrow-returning call is a borrowed source whatever its receiver is, so the
# TEMPORARY receiver below no longer exempts it. The copy is the ACKNOWLEDGED
# CPython divergence (CPython appends the very Payload the Holder holds), so
# main prints only what both sides agree on -- the element stays writable
# through the container either way -- and the WARNING is the pin.
from tpy import Int32, copy


class Payload:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Holder:
    p: Payload

    def __init__(self) -> None:
        self.p = Payload(42)

    def borrow(self) -> Payload:
        return self.p


def main() -> None:
    xs: list[Payload] = []
    # The receiver is a temporary, but `borrow` still hands back a borrow.
    xs.append(Holder().borrow())  # tpyc: warning(/copies Payload into owned storage/)
    # The copy() twin says the same thing explicitly and is silent.
    xs.append(copy(Holder().borrow()))  # tpyc: ok
    print(xs[0].v, xs[1].v)
    xs[0].v = 99
    print(xs[0].v, xs[1].v)


main()
