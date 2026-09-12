# A readonly subject reached through an earlier isinstance narrowing is still
# readonly through a match capture. Narrowing replaces the subject's EXPR type
# with the bare member, so the qualifier survives only on the scope binding --
# the sibling case error_match_capture_readonly_mutation covers the
# un-narrowed spelling, and this one pins the scope-binding fallback.
# Compilation stops at the first error, so one position only.
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
    if isinstance(a, Cat):
        match a:
            case Cat() as c:
                c.hunger -= 1  # tpyc: error(/Cannot mutate readonly reference/)


def main() -> None:
    feed(Cat(5))


main()
