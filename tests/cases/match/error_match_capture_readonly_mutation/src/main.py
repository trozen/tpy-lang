# An explicit `readonly[...]` annotation is enforced through a `match`-arm
# capture, not only through the isinstance twin: the capture aliases readonly
# storage, so it is readonly too. The happy-path positions live in
# match/match_capture_mutation_credited; the compiler stops at the first
# error, so this case takes the one representative position.
from tpy import int32, readonly


class Cat:
    hunger: int32

    def __init__(self, hunger: int32) -> None:
        self.hunger = hunger


class Dog:
    bones: int32

    def __init__(self, bones: int32) -> None:
        self.bones = bones


def feed(a: readonly[Cat | Dog]) -> None:
    match a:
        case Cat() as c:
            c.hunger -= 1  # tpyc: error(/Cannot mutate readonly reference/)
        case Dog() as d:
            d.bones += 1


def main() -> None:
    feed(Cat(5))


main()
