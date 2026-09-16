from tpy import int32


class Rec:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Base:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self, r: Rec) -> None:
        r.v += 1


class Factory(Base):
    # Mutates its ARGUMENT but not self, so auto-const inference marks it
    # readonly -- the flag a caller must not read as "mutates nothing".
    def touch(self, r: Rec) -> None:
        r.v += 1

    def touch_get(self, r: Rec) -> int32:
        r.v += 1
        return r.v

    @staticmethod
    def stouch(r: Rec) -> None:
        r.v += 1

    @classmethod
    def ctouch(cls, r: Rec) -> None:
        r.v += 1

    def via_super(self, r: Rec) -> None:
        super().bump(r)


class Anchor:
    seen: int32

    def __init__(self, r: Rec) -> None:
        r.v += 1
        self.seen = r.v


def touch_free(r: Rec) -> None:
    r.v += 1
