# super().__init__() covers only the MRO-first __init__ base (HasInitA);
# HasInitB still needs an explicit BaseN.__init__ call to satisfy coverage.
# A call reaching an `__init__` past an `__init__`-less direct base initializes
# that direct base.
from tpy import int32


class HasInitA:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


class HasInitB:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


class Combined(HasInitA, HasInitB):
    def __init__(self, a: int32, b: int32) -> None:
        super().__init__(a)              # covers HasInitA (MRO-first)
        HasInitB.__init__(self, b)       # HasInitB still needs an explicit call


class Root:
    r: int32

    def __init__(self, r: int32) -> None:
        print("root init", r)
        self.r = r


class Side:
    s: int32

    def __init__(self, s: int32) -> None:
        print("side init", s)
        self.s = s


class Lane(Root):
    pass


# super() resolves to Root.__init__ past the `__init__`-less direct base Lane,
# so it initializes Lane, and Side still needs its own call
class ViaSuper(Lane, Side):
    def __init__(self) -> None:
        super().__init__(1)  # tpyc: ok
        Side.__init__(self, 2)
        print("via-super: body")


# naming the `__init__`-less direct base
class ViaLane(Lane, Side):
    def __init__(self) -> None:
        Lane.__init__(self, 3)  # tpyc: ok
        Side.__init__(self, 4)
        print("via-lane: body")


# super() initializes Side (first in MRO); naming Root reaches it through Lane
class ViaRoot(Side, Lane):
    def __init__(self) -> None:
        super().__init__(5)
        Root.__init__(self, 6)  # tpyc: ok
        print("via-root: body")


def main() -> None:
    c = Combined(int32(10), int32(20))
    print(c.a)
    print(c.b)
    vs = ViaSuper()
    print("via-super:", vs.r, vs.s)
    vl = ViaLane()
    print("via-lane:", vl.r, vl.s)
    vr = ViaRoot()
    print("via-root:", vr.r, vr.s)


main()
