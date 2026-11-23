# Records and callees the tuple-member sections share.
from tpy import int32


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def reset(self) -> None:
        self.v = 0


class S(P):
    def __init__(self, v: int32) -> None:
        super().__init__(v)


def touch(p: P) -> None:
    p.v += 1


def bump_base(p: P) -> None:
    p.v += 10


def take_opt(o: P | None) -> int32:
    if o is not None:
        o.v += 1
        return o.v
    return 0


class Holder:
    p: P

    def __init__(self) -> None:
        self.p = P(0)
