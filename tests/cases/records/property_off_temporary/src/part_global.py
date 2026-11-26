# Reads off a temporary that lend storage the temporary does not own: an enum
# member's static name, a global, an argument handed back.
from enum import Enum
from tpy import Own, StrView, int32


class Color(Enum):
    RED = 1
    BLUE = 2

    @property
    def nm(self) -> StrView:
        return self.name


def get() -> Color:
    return Color.BLUE


class Inner:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __lt__(self, o: "Inner") -> bool:
        return self.v < o.v


GI = Inner(10)


class H:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    @property
    def gprop(self) -> Inner:
        return GI

    def through(self, i: Inner) -> Inner:
        return i


def mk(n: int32) -> Own[H]:
    return H(n)


def main() -> None:
    # an enum getter off a call receiver: the name is static storage.
    x = get().nm  # tpyc: ok
    print("enum_prop:", x)
    # a getter lending a global as a `key=` lambda body.
    ys = sorted([3, 1, 2], key=lambda r: mk(r).gprop)  # tpyc: ok
    print("lambda_key:", ys)
    # a method off a temporary handing back its argument (a global).
    y = mk(1).through(GI)  # tpyc: ok
    y.v = 11
    print("through_arg:", GI.v)
    # the getter lending a global, bound and written through.
    z = mk(2).gprop  # tpyc: ok
    z.v = 12
    print("gprop_decl:", GI.v)
