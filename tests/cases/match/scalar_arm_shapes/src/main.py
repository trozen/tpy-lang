# Three match arm shapes: a non-lvalue (call) subject, an or-group whose
# wildcard alternative makes it the catch-all, and `case x as y` binding the
# whole subject under two names -- the last one on each of the four tiers
# that render two binding lines (scalar chain/switch, the Optional tier's
# inner scalar switch, the str discriminator switch, the polymorphic chain).

from typing import Optional, Protocol

from tpy import Int32, dynamic


class Counter:
    hits: Int32

    def __init__(self) -> None:
        self.hits = 0

    def bump(self) -> Int32:
        self.hits += 1
        return self.hits


@dynamic
class Pet(Protocol):
    def bump(self) -> Int32: ...


class Dog(Pet):
    hits: Int32

    def __init__(self) -> None:
        self.hits = 0

    def bump(self) -> Int32:
        self.hits += 1
        return self.hits


class Cat(Pet):
    hits: Int32

    def __init__(self) -> None:
        self.hits = 0

    def bump(self) -> Int32:
        self.hits += 2
        return self.hits


def label() -> str:
    return "a"


def chain_call_subject() -> str:
    # The subject is a call, so it materializes into an owned dispatch local
    # instead of aliasing storage.
    match label():
        case "a":
            return "a"
        case _:
            return "other"


def guarded_call_subject(flag: bool) -> str:
    match label():
        case "a" if flag:
            return "a-flag"
        case "a":
            return "a-plain"
        case _:
            return "other"


def switch_call_subject(c: Counter) -> Int32:
    # A method-call subject on the primitive switch; the receiver mutates, so
    # a stale copy of the subject would show up in the caller's next read.
    match c.bump():
        case 1:
            return 10
        case _:
            return 20


def str_switch_call_subject() -> Int32:
    # Five unguarded literals route to the discriminator switch, and the
    # trailing or-group is its catch-all rather than an "f" bucket. The
    # warning is WRONG: the or-group IS a catch-all, but exhaustiveness does
    # not credit a wildcard OR-ALTERNATIVE, so every or-group arm below
    # warns (BUGS.md#match-exhaustiveness-or-wildcard).
    match label():  # tpyc: warning(/non-exhaustive match on 'str'/)
        case "a":
            return 1
        case "b":
            return 2
        case "c":
            return 3
        case "d":
            return 4
        case "e":
            return 5
        case "f" | _:
            return 0
    return -1


def or_wildcard_switch(n: Int32) -> str:
    # The wildcard alternative subsumes the literal beside it, so the group
    # dispatches as the switch default.
    match n:  # tpyc: warning(/non-exhaustive match on 'Int32'/)
        case 1 | _:
            return "any"
    return "unreached"


def or_wildcard_chain(s: str) -> str:
    match s:  # tpyc: warning(/non-exhaustive match on 'str'/)
        case "a":
            return "a"
        case "b" | _:
            return "rest"
    return "unreached"


def as_capture(n: Int32) -> Int32:
    # Both names bind the whole subject.
    match n:
        case x as y:
            return x + y
    return -1


def optional_inner_as_capture(n: Optional[Int32]) -> Int32:
    # The Optional tier partitions None off and hands the SCALAR payload to
    # the inner switch, so the double bind renders there rather than on the
    # Optional arm emit (which binds once and still rejects it,
    # BUGS.md#match-as-capture-composite-tiers).
    match n:
        case None:
            return 0
        case a as b:
            return a + b


def str_switch_as_capture(s: str) -> Int32:
    # `case x as y` on the discriminator switch's TRAILING arm: five
    # unguarded literals take the switch tier, and both names of the arm
    # after it bind the whole subject. `str` is a value type, so a copy is
    # unobservable here by construction -- the leg reads through both names
    # rather than mutating.
    match s:
        case "a":
            return 1
        case "b":
            return 2
        case "c":
            return 3
        case "d":
            return 4
        case "e":
            return 5
        case rest as also:
            print(rest)
            print(also)
            return len(rest) + len(also)


def poly_as_capture(p: Pet) -> Int32:
    # `case x as y` on the polymorphic chain: both names alias the SAME
    # object, so every mutation through either is visible to the caller.
    # The leading `p.bump()` is load-bearing: a mutation made only through a
    # match capture is not credited to the parameter, which then emits as
    # `const Pet&` and fails the C++ build
    # (BUGS.md#match-capture-mutation-not-credited).
    p.bump()
    match p:
        case Dog():
            return -1
        case seen as also:
            seen.bump()
            return also.bump()


def main() -> None:
    print(chain_call_subject())
    print(guarded_call_subject(True))
    print(guarded_call_subject(False))
    c = Counter()
    print(switch_call_subject(c))
    print(switch_call_subject(c))
    print(c.hits)
    print(str_switch_call_subject())
    print(or_wildcard_switch(1))
    print(or_wildcard_switch(7))
    print(or_wildcard_chain("a"))
    print(or_wildcard_chain("b"))
    print(or_wildcard_chain("z"))
    print(as_capture(3))
    print(optional_inner_as_capture(3))
    print(optional_inner_as_capture(None))
    print(str_switch_as_capture("c"))
    print(str_switch_as_capture("zz"))
    print(poly_as_capture(Dog()))
    cat = Cat()
    print(poly_as_capture(cat))
    # 6: both captures aliased `cat` rather than copying it.
    print(cat.hits)


main()
