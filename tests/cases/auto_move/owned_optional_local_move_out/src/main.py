# An owning `Foo | None` local MOVES out at its last use into every owned sink
# (field/return/ctor-arg+MIL; @nocopy forces the move), copies at a non-last
# use, and keeps the borrow form so aliasing (alias_then_rebind) matches CPython.
from tpy import Own, nocopy


@nocopy
class Box:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Holder:
    slot: Box | None

    def __init__(self) -> None:
        self.slot = None


class Boxed:
    slot: Box | None

    def __init__(self, b: Own[Box] | None) -> None:
        self.slot = b            # MIL: move the Own-optional param into the field


class Pt:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def move_owned_optional_into_field() -> int:
    h = Holder()
    tmp: Box | None = Box(7)
    h.slot = tmp                 # tmp's last use -> move (copy would be a @nocopy error)
    if h.slot is not None:
        return h.slot.v
    return -1


def move_owned_optional_out_return(c: bool) -> Own[Box] | None:
    tmp: Box | None = None
    if c:
        tmp = Box(11)
    return tmp                   # last use -> move the slot out as Own[Box] | None


def move_owned_optional_into_ctor_arg() -> int:
    tmp: Box | None = Box(13)
    b = Boxed(tmp)               # tmp last use -> move into the Own[Box]|None param
    if b.slot is not None:
        return b.slot.v
    return -1


def alias_then_rebind() -> int:
    x: Pt | None = Pt(1)
    y: Pt | None = x             # y aliases x's object
    if x is not None:
        x.v = 99                 # mutate the shared object
    seen = -1
    if y is not None:
        seen = y.v               # 99 -- alias sees the mutation
    x = Pt(2)                    # rebind x to a new object; y unaffected
    after = -1
    if y is not None:
        after = y.v              # still 99
    return seen + after          # 198


def main() -> None:
    print(move_owned_optional_into_field())
    r = move_owned_optional_out_return(True)
    if r is not None:
        print(r.v)
    print(move_owned_optional_out_return(False) is None)
    print(move_owned_optional_into_ctor_arg())
    print(alias_then_rebind())


main()
