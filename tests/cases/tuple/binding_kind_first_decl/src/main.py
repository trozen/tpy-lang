# A first declaration is built for the binding kind the planner picks: a tuple
# rebound to itself, the shapes whose kind the planner took over from the
# declaration arms, and tuple-literal elements constructed in place.
from dataclasses import asdict, astuple, dataclass
from enum import Enum
from typing import TypedDict, cast

from tpy import Own, int32, readonly


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


@dataclass
class D:
    a: int32
    b: int32


class TD(TypedDict):
    k: int32


class Color(Enum):
    RED = 1
    BLUE = 2


class H:
    pair: tuple[Box, int32]

    def __init__(self) -> None:
        self.pair = (Box(1), 2)


def self_rebind_owned() -> None:
    # self_rebind_owned: `t = t` over a tuple that owns its elements.
    # a and b die at the literal, so the tuple holds them; `t = t` keeps it.
    a = Box(1)
    b = Box(2)
    t = (a, b)
    t = t  # tpyc: ok
    t[0].v = 5
    print("self_rebind_owned", t[0].v, t[1].v)


def self_rebind_borrowed() -> None:
    # self_rebind_borrowed: `t = t` over a tuple that refers to live names.
    # a outlives the tuple, so the tuple refers to it: a write through the
    # tuple after the rebind shows on a.
    a = Box(1)
    b = Box(2)
    t = (a, b)
    t = t  # tpyc: ok
    t[0].v = 7
    print("self_rebind_borrowed", a.v, b.v)


# The warning is right: `y = x` binds a second name for the param's value
# and nothing moves it out, so the handed-over value is never consumed.
def own_param_lift(x: Own[Box | None]) -> int32:  # tpyc: warning(/never consumed/)
    # own_param_lift: a name bound off an owning Optional param.
    # `y = x` off an owning Optional param is a second name for the param's
    # value: a write through y shows on x.
    y = x  # tpyc: ok
    if y is not None:
        y.v += 1
    if x is not None:
        return x.v
    return 0


def read_pair(h: readonly[H]) -> int32:
    # read_pair: a tuple field bound off a readonly receiver.
    t = h.pair  # tpyc: ok
    return t[0].v + t[1]


def storage_alias() -> None:
    # storage_alias: `t = h.pair` binds the field's storage tuple.
    # `t = h.pair` aliases the field's tuple: a write through t shows on h.
    h = H()
    t = h.pair  # tpyc: ok
    t[0].v = 5
    print("storage_alias", h.pair[0].v, read_pair(h))


def pick(a: Box) -> tuple[Box, int32]:
    return (a, 7)


def rebound_call_source(a: Box, b: Box) -> None:
    # rebound_call_source: a rebound borrow tuple whose later write is a call.
    # A reassigned tuple whose later source is a borrow-tuple call: the
    # literal first declaration still refers to a.
    t = (a, 1)  # tpyc: ok
    t[0].v += 10
    s = t[0].v
    t = pick(b)
    t[0].v += 100
    print("rebound_call_source", s, a.v, b.v)


def elem_cast() -> None:
    # elem_cast: a `cast` passthrough built in place as a literal element.
    t = (cast(Box, Box(1)), 2)  # tpyc: ok
    t[0].v += 1
    print("elem_cast", t[0].v, t[1])


def elem_typed_dict() -> None:
    # elem_typed_dict: a TypedDict constructor as a literal element.
    t = (TD(k=3), Box(4))  # tpyc: ok
    t[1].v += 1
    # Bound first: `t[0]["k"]` rejects,
    # BUGS.md#tuple-typed-dict-member-subscript-rejects.
    td = t[0]
    print("elem_typed_dict", td["k"], t[1].v)


def elem_enum_value() -> None:
    # elem_enum_value: an enum from its value as a literal element.
    t = (Color(2), Box(5))  # tpyc: ok
    t[1].v += 1
    print("elem_enum_value", t[0].name, t[1].v)


def elem_call_macro() -> None:
    # elem_call_macro: a call macro expands in place; its expansion is the
    # element.
    d = D(1, 2)
    t = (astuple(d), Box(6))  # tpyc: ok
    print("elem_call_macro", t[0], t[1].v)


def elem_asdict() -> None:
    # elem_asdict: a dict-building call macro as a literal element (the
    # dict member is not printed: BUGS.md#tuple-dict-member-print-rejects).
    d = D(1, 2)
    t = (asdict(d), 3)  # tpyc: ok
    print("elem_asdict", t[1])


class K:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def method_self_rebind(self) -> None:
        # method_self_rebind: `t = t` in a method body.
        a = Box(1)
        b = Box(2)
        t = (a, b)
        t = t  # tpyc: ok
        t[0].v = 7
        print("method_self_rebind", a.v, b.v)


def branch_first_self_rebind(c: bool, a: Box, b: Box) -> None:
    # branch_first_self_rebind: a tuple first declared in a branch off a
    # name, then rebound to itself; the write shows on a.
    s = (a, b)
    if c:
        t = s
        t = t  # tpyc: ok
        t[0].v = 9
        print("branch_first_self_rebind", a.v)


def loop_self_rebind(a: Box, b: Box) -> None:
    # loop_self_rebind: `t = t` in a loop body; each pass writes through to a.
    for _ in range(2):
        t = (a, b)
        t = t  # tpyc: ok
        t[0].v += 1
    print("loop_self_rebind", a.v)


def main() -> None:
    self_rebind_owned()
    self_rebind_borrowed()
    print("own_param_lift", own_param_lift(Box(3)), own_param_lift(None))
    storage_alias()
    rebound_call_source(Box(1), Box(2))
    elem_cast()
    elem_typed_dict()
    elem_enum_value()
    elem_call_macro()
    elem_asdict()
    K().method_self_rebind()
    branch_first_self_rebind(True, Box(1), Box(2))
    loop_self_rebind(Box(1), Box(2))


main()
