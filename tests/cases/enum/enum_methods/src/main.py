# Methods on enums (instance, property, static, classmethod) called through
# every receiver shape and from every position.
import asyncio
from enum import Enum, IntEnum
from typing import Callable, Iterator, Optional, Self
from tpy import int32, try_parse, readonly, pure, error_return, ReturnException


class Bad(Exception, ReturnException):
    pass


class Color(Enum):
    Red = 0
    Blue = 2

    # instance method: `self` is the member
    @readonly
    @pure
    def label(self) -> str:  # tpyc: ok
        return "<" + self.name + ">"

    def other(self) -> Self:  # tpyc: ok
        return Color.Blue if self == Color.Red else Color.Red

    # instance method calling another through `self`, chained
    def chain(self) -> str:
        return self.other().label()  # tpyc: ok

    # match on `self`
    def kind(self) -> str:
        match self:  # tpyc: ok
            case Color.Red:
                return "warm"
            case Color.Blue:
                return "cold"

    # defaults, a keyword-only param, and a positional-only param after the receiver
    def repeat(self, n: int32 = 2, *, sep: str = "-") -> str:
        out = ""
        for i in range(n):
            if i:
                out += sep
            out += self.label()
        return out

    def pair(self) -> tuple[str, int32]:
        return (self.label(), self.value)

    def scaled(self, k: int32, /) -> int32:
        return self.value * k

    # property: read without a call, on the member by value
    @property
    def warm(self) -> bool:
        return self == Color.Red  # tpyc: ok

    # a str method straight off the intrinsic `.name` (the receiver gate)
    def lower_name(self) -> str:
        return self.name.lower()  # tpyc: ok

    # position: @error_return body, raising through the companion method
    @error_return(Bad)
    def checked(self, k: int32) -> int32:
        if k < 0:
            raise Bad()  # tpyc: ok
        return self.value + k

    # position: closure -- a nested def and a lambda capture `self`, the
    # lambda escaping the call
    def shout(self) -> str:
        def inner() -> str:
            return self.name.upper()  # tpyc: ok
        return inner()

    def later(self) -> Callable[[], int32]:
        return lambda: self.value  # tpyc: ok

    def later_def(self) -> Callable[[], int32]:
        def inner() -> int32:
            return self.value + 1  # tpyc: ok
        return inner

    # a property whose value is subscripted and called (`c.fns[0]()`)
    @property
    def fns(self) -> list[Callable[[], int32]]:
        return FNS

    # generic method: called on `self`, a list passes through by reference
    def tag[T](self, x: T) -> T:
        return x

    def pass_on(self, ys: list[int32]) -> list[int32]:
        return self.tag(ys)  # tpyc: ok

    # position: comprehension over `self`
    def spread(self) -> int32:
        xs = [self.value + i for i in range(3)]  # tpyc: ok
        return xs[2]

    # an Enum member with value 0 is truthy (only IntEnum reads the integer)
    def truthy(self) -> bool:
        return True if self else False  # tpyc: ok

    # classmethod: `cls` is the enum, never a value
    @classmethod
    def parse(cls, s: str) -> Optional[Self]:
        return try_parse(cls, s)  # tpyc: ok

    @classmethod
    def first(cls) -> Self:
        for m in cls:  # tpyc: ok
            return m
        return cls(0)

    @classmethod
    def by_value(cls, v: int32) -> Self:
        return cls(v)  # tpyc: ok

    @classmethod
    def by_name(cls, s: str) -> Self:
        return cls[s]  # tpyc: ok

    @classmethod
    def via_static(cls) -> Self:
        return cls.default()  # tpyc: ok

    @staticmethod
    def default() -> "Color":
        return Color.Red


class Level(IntEnum):
    Low = 0
    High = 3

    # IntEnum: `self` takes part in integer arithmetic
    def bump(self) -> int32:
        return self + 1  # tpyc: ok

    # IntEnum 0 is falsy
    def truthy(self) -> bool:
        return True if self else False

    @classmethod
    def top(cls) -> Self:
        best = cls.Low
        for m in cls:  # tpyc: ok
            if m > best:
                best = m
        return best

    @staticmethod
    def floor() -> "Level":
        return Level.Low


calls = 0
log: list[str] = []


def one() -> int32:
    return 1


FNS: list[Callable[[], int32]] = [one]


def pick() -> Color:
    global calls
    calls += 1
    log.append("pick")
    return Color.Blue


def arg() -> int32:
    log.append("arg")
    return 2


class Holder:
    tag: str

    def __init__(self, c: Color) -> None:
        self.c = c
        # position: constructor body
        self.tag = c.label()  # tpyc: ok

    # position: record method, enum-valued field receiver (instance and
    # static)
    def describe(self) -> str:
        return self.c.label() + "/" + self.c.default().label() + "/" + self.tag  # tpyc: ok


async def tick() -> None:
    await asyncio.sleep(0)


async def arun(c: Color) -> str:
    # position: async body, a call on each side of a suspension
    first = c.label()  # tpyc: ok
    await tick()
    return first + c.other().label()


class Guard:
    def __enter__(self) -> None:
        pass

    def __exit__(self, kind, value, tb) -> None:
        pass


def gen(c: Color) -> Iterator[str]:
    # position: generator body
    yield c.label()  # tpyc: ok
    yield c.other().label()


def main() -> None:
    c = Color.Red
    # position: free function; instance, chained, match, defaults, tuple
    print("instance:", c.label(), c.other(), c.chain(), c.kind())
    print("defaults:", c.repeat(), c.repeat(3, sep="+"), c.repeat(n=1), c.repeat(n=2, sep="."))
    print("posonly:", c.scaled(3), Color.Blue.scaled(2))
    s, v = c.pair()
    print("tuple:", s, v)
    print("truthy:", Color.Red.truthy(), Level.Low.truthy(), Level.High.truthy())
    # printed by name: CPython 3.12 prints an IntEnum member as its integer
    print("intenum:", Level.High.bump(), Level.Low.bump(), Level.top().name, Level.High.floor().name, Level.Low.top().name)
    # classmethod / staticmethod through the type
    print("type:", Color.parse("Blue"), Color.parse("Nope"), Color.first(),
          Color.by_value(2), Color.by_name("Red"), Color.via_static(), Color.default())
    # static / classmethod through a member (a name or a member literal)
    print("member:", c.default(), c.parse("Blue"), Color.Red.default())  # tpyc: ok
    # a static through a call receiver still evaluates the receiver once
    print("effect:", pick().default(), pick().label(), calls)  # tpyc: ok
    # an @error_return enum method under try, succeeding then failing (bound
    # before printing: BUGS.md#print-arg-output-interleaves)
    try:
        ok = c.checked(1)  # tpyc: ok
        print("checked:", ok)
        bad = Color.Blue.checked(-1)
        print("checked:", bad)
    except Bad:
        print("checked: bad")
    # the receiver is evaluated before the arguments
    log.clear()
    print("order:", pick().scaled(arg()), log)  # tpyc: ok
    # receiver shapes: a conditional expression and a tuple element
    flag = True
    t = (Color.Blue, 1)
    print("shapes:", (c if flag else Color.Blue).label(), t[0].label())  # tpyc: ok
    print("property call:", c.fns[0]())  # tpyc: ok
    # the list a generic method returns is the caller's list, not a copy
    ys = [9]
    zs = c.pass_on(ys)
    zs.append(10)
    print("generic alias:", ys, zs)
    print("capture:", c.shout(), Color.Blue.later()(), Color.Blue.later_def()(), c.spread())
    # property read on a name, a member literal and a call result
    print("property:", c.warm, Color.Blue.warm, pick().warm)  # tpyc: ok
    # str method off `.name` of a name, a member literal and a field
    print("name:", c.name.lower(), Color.Blue.name.upper(), c.lower_name())  # tpyc: ok
    # Optional[Self] result consumed through narrowing
    p = Color.parse("Blue")
    if p is not None:
        print("narrowed:", p.label())  # tpyc: ok
    # receivers stored in containers
    xs = [Color.Red, Color.Blue]
    d = {"k": Color.Blue}
    print("container:", xs[1].label(), d["k"].kind())  # tpyc: ok
    print("record:", Holder(Color.Blue).describe())
    # position: comprehension
    print("comprehension:", [x.label() for x in [Color.Red, Color.Blue]])  # tpyc: ok
    # position: closure
    def inner() -> str:
        return c.chain()  # tpyc: ok
    print("closure:", inner())
    # position: match arm
    match v:
        case 0:
            print("match:", c.label())  # tpyc: ok
        case _:
            print("match: other")
    # position: context-manager body
    with Guard():
        print("with:", c.other().label())  # tpyc: ok
    # position: try/finally
    try:
        print("try:", Color.Blue.label())
    finally:
        print("finally:", Color.Blue.other().label())  # tpyc: ok
    print("generator:", list(gen(c)))
    print("async:", asyncio.run(arun(c)))
    # statement loop over the enum type
    for m in Color:
        print("loop:", m.label(), m.kind())


main()
# position: module-level statement
print("module:", Color.default().label(), Color.Blue.other().label())  # tpyc: ok
