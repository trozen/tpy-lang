# A tuple LITERAL element that is a value-union-param call, where the tuple
# crosses a borrow boundary (a tuple param binds by reference, routing rvalue
# elements through tuple_value_to_borrow), must evaluate that inner call once
# (regression: the borrow path rendered the element twice -- borrow form then
# value form -- doubling side effects and leaking a hoisted temp; the
# tuple-literal analog of the method-arg double-eval). Each "eval <tag>" once.
from dataclasses import dataclass
from tpy import ValueType, int32


@dataclass(frozen=True)
class Fixed(ValueType):
    off: int32


@dataclass(frozen=True)
class Zone(ValueType):
    zid: int32


class Box:
    v: int32

    def __init__(self, tz: Fixed | Zone | None = None) -> None:
        if tz is None:
            self.v = 0
        elif isinstance(tz, Fixed):
            self.v = int(tz.off)
        else:
            self.v = int(tz.zid)


def mk(tag: str, n: int) -> Fixed:
    print("eval " + tag)
    return Fixed(n)


def take(pair: tuple[Box, int32]) -> int:
    return int(pair[0].v) + int(pair[1])


def take2(pair: tuple[Box, Box]) -> int:
    return int(pair[0].v) + int(pair[1].v)


def take_opt(pair: tuple[Box | None, int32]) -> int:
    # Reads only the int32 slot: reading the Optional-Box element hits a
    # separate pre-existing const-propagation bug (BUGS.md). The point here
    # is the CONSTRUCTION of the rvalue element into the Optional-borrow
    # slot, which exercises the fix's elem_target.inner unwrap arm.
    return int(pair[1])


def main() -> None:
    # Rvalue Box(mk(...)) element in a borrow-slot tuple: inner mk() once.
    print(take((Box(mk("fixed", 3)), 5)))
    # Two rvalue-into-borrow elements: each inner call once.
    print(take2((Box(mk("a", 4)), Box(mk("b", 6)))))
    # Rvalue into an Optional-borrow slot (tuple[Box | None, ...]): exercises
    # the elem_target.inner unwrap; inner mk() once.
    print(take_opt((Box(mk("opt", 8)), 4)))
    # Zone arm through the same shape (no mk print): value flows correctly.
    print(take((Box(Zone(7)), 2)))


main()
