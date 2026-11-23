# A readonly payload -- `readonly[T] | None`, `Optional[readonly[T]]`,
# `Send[readonly[T]] | None` -- at a method and a constructor takes the const
# trait; a class argument stays the caller's object.
from typing import Optional

from tpy import Send, readonly

from fixtures import P, V


class RoProbe[T]:
    has: bool

    def __init__(self, x: readonly[T] | None) -> None:
        self.has = x is not None  # tpyc: ok

    def probe(self, x: readonly[T] | None) -> bool:
        print("ro_method", x)  # tpyc: ok
        return x is not None


class OptRoProbe[T]:
    has: bool

    def __init__(self, x: Optional[readonly[T]]) -> None:
        self.has = x is not None  # tpyc: ok

    def probe(self, x: Optional[readonly[T]]) -> bool:
        print("opt_ro_method", x)  # tpyc: ok
        return x is not None


class SendRoProbe[T]:
    has: bool

    def __init__(self, x: Send[readonly[T]] | None) -> None:
        self.has = x is not None  # tpyc: ok

    def probe(self, x: Send[readonly[T]] | None) -> bool:
        print("send_ro_method", x)  # tpyc: ok
        return x is not None


def main() -> None:
    p = P(8)
    print("ro_ctor", RoProbe(1).has, RoProbe(V(2)).has, RoProbe("s").has,
          RoProbe(p).has)
    print("opt_ro_ctor", OptRoProbe(1).has, OptRoProbe(V(2)).has,
          OptRoProbe("s").has, OptRoProbe(p).has)
    print("send_ro_ctor", SendRoProbe(1).has, SendRoProbe(V(2)).has,
          SendRoProbe("s").has, SendRoProbe(p).has)
    # `P` is @nocopy, so the const trait holds the caller's object. The methods
    # print, so each result is bound before its line prints.
    a = RoProbe(1).probe(2)
    b = RoProbe(p).probe(p)
    print("ro_method", a, b)
    a = OptRoProbe(V(3)).probe(V(4))
    b = OptRoProbe(p).probe(p)
    print("opt_ro_method", a, b)
    a = SendRoProbe("s").probe("t")
    b = SendRoProbe(p).probe(p)
    print("send_ro_method", a, b)
