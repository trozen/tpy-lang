# A BORROW-returning method call at an OWNING record return slot
# (`-> Own[Payload]`): the slot copies, and says so with the same warning the
# insert / `Own[T]` parameter / `yield` slots emit. The copy is an
# ACKNOWLEDGED CPython divergence -- CPython hands back the very Payload, so
# mutating the result would show at `h.p.n` there and not here. The cpy phase
# byte-compares output, so this case prints only what both agree on and the
# WARNING is the pin; `take_copy` is the spelling that silences it.
from tpy import Int32, Own, copy


class Payload:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Holder:
    p: Payload

    def __init__(self) -> None:
        self.p = Payload(1)

    def borrow(self) -> Payload:
        return self.p


def take(h: Holder) -> Own[Payload]:
    # `h.borrow()` hands back `Payload&`; filling the owning slot from it is
    # the copy the warning declares.
    return h.borrow()  # tpyc: warning(/copies Payload into owned storage/)


def take_copy(h: Holder) -> Own[Payload]:
    # The explicit spelling: same copy, no warning.
    return copy(h.borrow())  # tpyc: ok


def main() -> None:
    h = Holder()
    print(take(h).n, take_copy(h).n, h.p.n)


main()
