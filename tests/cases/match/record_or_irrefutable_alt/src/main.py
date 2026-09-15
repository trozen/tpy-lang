# An or-pattern alternative that renders NO condition is irrefutable -- it
# matches every value of the subject's type -- so the whole arm matches
# however constrained its siblings are, exactly as CPython dispatches it.
# Such an arm is the chain's always-match arm, so a match carrying one in a
# non-final position takes the ordered standalone-block tier. Sections: free
# function, non-final position, the Optional-of-record inner dispatch, method,
# generator.
from typing import Iterator, Optional

from tpy import Own, int32


class Cat:
    lives: int32

    def __init__(self, lives: int32) -> None:
        self.lives = lives


def plain(c: Cat) -> str:
    # `Cat()` matches any Cat, so `lives=3` cannot narrow the arm.
    match c:  # tpyc: ok
        case Cat() | Cat(lives=3):
            return "hit"
        case _:
            return "miss"


def nonfinal(c: Cat) -> str:
    # The irrefutable arm is not last, so the later arms are dead -- but they
    # still have to be rendered after it, not around it.
    match c:  # tpyc: ok
        case Cat() | Cat(lives=3):
            return "first"
        case Cat(lives=5):
            return "second"
        case _:
            return "other"


def opt_inner(o: Optional[Cat]) -> str:
    # The Optional partition's record-inner dispatch reads the same rule.
    match o:  # tpyc: ok
        case None:
            return "none"
        case Cat() | Cat(lives=3):
            return "cat"


class Shelter:
    resident: Cat

    def __init__(self, resident: Own[Cat]) -> None:
        self.resident = resident

    def status(self) -> str:
        match self.resident:  # tpyc: ok
            case Cat() | Cat(lives=3):
                return "in"
            case _:
                return "out"


def counts(c: Cat) -> Iterator[int32]:
    # Generator position, record chain. Exhaustiveness does not credit an
    # irrefutable or-alternative (BUGS.md#match-exhaustiveness-or-wildcard).
    match c:  # tpyc: warning(/non-exhaustive match on 'Cat'/)
        case Cat(lives=1):
            yield 1
        case Cat() | Cat(lives=3):
            yield 2
    yield 9


def counts_ordered(c: Cat, k: bool) -> Iterator[int32]:
    # Generator position on the ORDERED record tier: a guard, and the
    # irrefutable arm before a later one. The dispatch hook admits both
    # record tiers, so the arm bodies stay frame states.
    match c:  # tpyc: ok
        case Cat(lives=1) if k:
            yield 1
            yield 11
        case Cat() | Cat(lives=3):
            yield 2
        case _:
            yield 3
    yield 9


def main() -> None:
    print("plain:", plain(Cat(3)), plain(Cat(5)))
    print("nonfinal:", nonfinal(Cat(3)), nonfinal(Cat(5)), nonfinal(Cat(7)))
    print("opt_inner:", opt_inner(None), opt_inner(Cat(3)), opt_inner(Cat(5)))
    print("method:", Shelter(Cat(3)).status(), Shelter(Cat(5)).status())
    for v in counts(Cat(1)):
        print("gen_one:", v)
    for v in counts(Cat(5)):
        print("gen_five:", v)
    for v in counts_ordered(Cat(1), True):
        print("gen_guarded:", v)
    for v in counts_ordered(Cat(1), False):
        print("gen_unguarded:", v)


main()
