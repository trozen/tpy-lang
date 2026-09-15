# A polymorphic `match` whose subject is POINTER-shaped (`Ptr[Pet]`) spells
# the dynamic_cast argument bare -- the subject already IS the pointer the
# cast takes, where a bare `Pet&` subject needs `&subject`. Sections: class
# arms, an or-arm, a guarded arm (the standalone-block tier), a capture that
# ALIASES (mutated after the match and read back), method position, and a
# readonly POINTEE.
from typing import Protocol

from tpy import Own, Ptr, dynamic, int32, readonly


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    walks: int32

    def __init__(self, walks: int32) -> None:
        self.walks = walks

    def speak(self) -> str:
        return "woof"


class Cat(Pet):
    naps: int32

    def __init__(self, naps: int32) -> None:
        self.naps = naps

    def speak(self) -> str:
        return "meow"


class Bird(Pet):
    def speak(self) -> str:
        return "tweet"


def kind(p: Ptr[Pet]) -> str:
    match p:  # tpyc: ok
        case Dog():
            return "dog"
        case Cat():
            return "cat"
        case _:
            return "other"


def furry(p: Ptr[Pet]) -> str:
    # An or-arm over a pointer subject: one ||-joined null test per cast.
    match p:  # tpyc: ok
        case Dog() | Cat():
            return "furry"
        case _:
            return "bare"


def loud(p: Ptr[Pet], k: bool) -> str:
    # A guard puts the match on the standalone-block tier.
    match p:  # tpyc: ok
        case Dog() if k:
            return "loud-dog"
        case Dog():
            return "dog"
        case _:
            return "other"


def walk(p: Ptr[Pet]) -> None:
    # The capture aliases the matched object, so the caller sees the bump.
    match p:  # tpyc: ok
        case Dog() as d:
            d.walks += 1
        case _:
            pass


def paws(p: Ptr[readonly[Pet]]) -> int32:
    # `Ptr[readonly[Pet]]` is `const Pet*`, so the whole cast pair has to be
    # const (`const Dog* = dynamic_cast<const Dog*>(...)`) -- a mutable target
    # casts away constness and g++ refuses it.
    match p:  # tpyc: ok
        case Dog() as d:
            return d.walks
        case _:
            return -1


class Shelter:
    def house(self, p: Ptr[Pet]) -> str:
        match p:  # tpyc: ok
            case Cat():
                return "cat-room"
            case _:
                return "yard"


def main() -> None:
    d = Dog(0)
    c = Cat(2)
    b = Bird()
    print("kind:", kind(d), kind(c), kind(b))
    print("furry:", furry(d), furry(b))
    print("loud:", loud(d, True), loud(d, False), loud(b, True))
    walk(d)
    walk(b)
    print("walk:", d.walks)
    print("method:", Shelter().house(c), Shelter().house(d))
    print("readonly:", paws(d), paws(b))


main()
