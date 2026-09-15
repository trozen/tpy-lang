# An or-group arm can carry the whole-subject `as` binding: every alternative
# matches the same subject, so there is one thing to bind and the binding line
# is the same in each. The record sections MUTATE through the capture and read
# the change back, so an accidental copy would show. Sections: record chain,
# record chain beside a guarded arm, the Optional chain over a scalar inner,
# the Optional-of-record inner dispatch, method.
from typing import Optional

from tpy import Own, int32


class Cat:
    lives: int32

    def __init__(self, lives: int32) -> None:
        self.lives = lives


def bump(c: Cat) -> None:
    # The capture aliases the subject -- the caller sees the increment.
    match c:  # tpyc: ok
        case Cat(lives=1) | Cat(lives=2) as q:
            q.lives += 10
        case _:
            pass


def bump_guarded(c: Cat, flag: bool) -> None:
    # A guard elsewhere puts the match on the standalone-block tier; the
    # unguarded or-group arm still binds.
    match c:  # tpyc: ok
        case Cat(lives=5) if flag:
            c.lives += 100
        case Cat(lives=1) | Cat(lives=2) as q:
            q.lives += 10
        case _:
            pass


def irrefutable_group(c: Cat) -> int32:
    # A wildcard alternative makes the group the catch-all; the `as` still
    # binds the subject. (On an Optional subject the same spelling rejects --
    # there the binding reads the deref, which a None subject has none of;
    # `tests/cases/match/error_optional_or_wildcard_as`.) Exhaustiveness does
    # not credit an irrefutable or-alternative
    # (BUGS.md#match-exhaustiveness-or-wildcard).
    match c:  # tpyc: warning(/non-exhaustive match on 'Cat'/)
        case Cat(lives=1) | _ as q:
            return q.lives
    return -1


def scalar_inner(o: Optional[int32]) -> int32:
    # The Optional chain binds the inner value, not the Optional.
    match o:  # tpyc: ok
        case 1 | 2 as x:
            return x * 10
        case None:
            return -1
        case _:
            return 0


def opt_record(o: Optional[Cat]) -> None:
    # The Optional partition's record-inner dispatch, same binding.
    match o:  # tpyc: ok
        case None:
            pass
        case Cat(lives=1) | Cat(lives=2) as q:
            q.lives += 10
        case _:
            pass


class Shelter:
    resident: Cat

    def __init__(self, resident: Own[Cat]) -> None:
        self.resident = resident

    def feed(self) -> None:
        match self.resident:  # tpyc: ok
            case Cat(lives=1) | Cat(lives=2) as q:
                q.lives += 10
            case _:
                pass


def main() -> None:
    a = Cat(2)
    bump(a)
    b = Cat(7)
    bump(b)
    print("bump:", a.lives, b.lives)
    c = Cat(1)
    bump_guarded(c, True)
    d = Cat(5)
    bump_guarded(d, True)
    print("bump_guarded:", c.lives, d.lives)
    print("irrefutable_group:", irrefutable_group(Cat(1)),
          irrefutable_group(Cat(4)))
    print("scalar_inner:", scalar_inner(2), scalar_inner(None),
          scalar_inner(9))
    e = Cat(2)
    opt_record(e)
    opt_record(None)
    print("opt_record:", e.lives)
    s = Shelter(Cat(1))
    s.feed()
    print("method:", s.resident.lives)


main()
