# Records shared by the sections: a P stored inline as an Optional field of H, H inline in W.
from tpy import int32


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def reset(self) -> None:
        self.v = 0


class H:
    o: P | None
    p: P

    def __init__(self, n: int32) -> None:
        self.o = None
        self.p = P(n + 10)
        if n > 0:
            self.o = P(n)


class W:
    h: H

    def __init__(self, n: int32) -> None:
        self.h = H(n)


def clear(h: H) -> None:
    h.o = None


def refill(h: H) -> None:
    h.o = P(5)


def empty_above(w: W) -> None:
    w.h = H(0)
