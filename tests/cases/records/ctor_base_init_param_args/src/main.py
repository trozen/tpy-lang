# Base-init argument forwarding: an `Optional[record]` parameter passes through,
# and an `Own[record]` one renders bare into the base's slot. Both are handed to
# the base by value, so nothing observes an alias here.
from tpy import int32, Own


class Payload:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Base:
    p: Payload
    opt: Payload | None

    def __init__(self, p: Payload, opt: Payload | None) -> None:
        self.p = p  # tpyc: warning(/copies Payload into field/)
        self.opt = opt  # tpyc: warning(/copies Payload \| None into field/)


class WithOptional(Base):
    def __init__(self, p: Payload, o: Payload | None) -> None:
        super().__init__(p, o)  # the Optional parameter argument


class WithOwn(Base):
    def __init__(self, q: Own[Payload]) -> None:  # tpyc: warning(/never consumed/)
        super().__init__(q, None)  # the Own parameter argument


def main() -> None:
    a = WithOptional(Payload(1), Payload(2))
    print(a.p.v, a.opt is None)
    b = WithOwn(Payload(3))
    print(b.p.v, b.opt is None)


main()
