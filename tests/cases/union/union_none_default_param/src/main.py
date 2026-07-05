# A `= None` default on a None-including union param must default-construct
# the variant's monostate arm (regression: it emitted `nullptr`). Record-
# materializing defaults (value variant, optional-of-record) work on member
# functions whose record alternatives are defined earlier; the free-function
# form is still open (BUGS.md: the decl precedes the record definitions,
# where C++ checks the default's conversion). Pointer variants stay complete
# at any decl, so that arm covers a free function.
from dataclasses import dataclass
from tpy import ValueType, Int32


@dataclass(frozen=True)
class Fixed(ValueType):
    off: Int32


@dataclass(frozen=True)
class Zone(ValueType):
    zid: Int32


class Dog:
    def __init__(self, barks: int) -> None:
        self.barks = barks


class Cat:
    def __init__(self, meows: int) -> None:
        self.meows = meows


class Holder:
    kind: Int32

    def __init__(self, tz: Fixed | Zone | None = None) -> None:
        k = 0
        if tz is not None:
            if isinstance(tz, Fixed):
                k = 1
            else:
                k = 2
        self.kind = k

    def describe(self, tz: Fixed | Zone | None = None) -> str:
        if tz is None:
            return "none"
        if isinstance(tz, Fixed):
            return "fixed:" + str(tz.off)
        return "zone:" + str(tz.zid)

    def opt(self, tz: Fixed | None = None) -> str:
        if tz is None:
            return "none"
        return "fixed:" + str(tz.off)


def pointer_arm(pet: Dog | Cat | None = None) -> str:
    if pet is None:
        return "none"
    if isinstance(pet, Dog):
        pet.barks += 1  # mutate through the union param: borrow, not a copy
        return "dog"
    return "cat"


def main() -> None:
    print(Holder().kind, Holder(Fixed(60)).kind, Holder(Zone(2)).kind)
    h = Holder()
    print(h.describe())
    print(h.describe(Fixed(60)))
    print(h.describe(Zone(7)))
    print(h.opt())
    print(h.opt(Fixed(5)))
    print(pointer_arm())
    d = Dog(1)
    print(pointer_arm(d), d.barks)


main()
