# Text agrees with CPython: enum str/repr per flavor (Enum, IntEnum, int mixin),
# a dunder-less record's default text, repr() of bool, a recursive alias's str().
import io
from enum import Enum, IntEnum
from typing import Iterator
from tpy import int8, int32
from other import Q


class Color(Enum):
    RED = 1
    GREEN = 2


class Num(IntEnum):
    ONE = 1
    TWO = 2


class Mix(int, Enum):
    A = 1


class Small(int8, Enum):
    A = -5


class Plain:
    pass


class Msg:
    class Kind(Enum):
        TEXT = 1


class Holder:
    c: Color
    n: Num

    def __init__(self) -> None:
        self.c = Color.GREEN
        self.n = Num.TWO

    def show(self) -> None:
        # method: enum fields through print, f-string and repr
        print("method", self.c, self.n, f"{self.c}|{self.n}", repr(self.c), repr(self.n))  # tpyc: ok


type J = str | list[J]


def printed(p: Plain) -> str:
    buf = io.StringIO()
    print(p, file=buf)
    return buf.getvalue()


def printed_any(q: Q) -> str:
    buf = io.StringIO()
    print(q, file=buf)
    return buf.getvalue()


def get_n() -> Num:
    return Num.ONE


def free_function() -> None:
    c = Color.RED
    n = Num.ONE
    m = Mix.A
    # str text: Enum and the int mixin `E.MEMBER`, IntEnum its value
    print("free", c, n, m)  # tpyc: ok
    print("free", f"{c}", f"{n}", f"{m}", f"{c!s}", f"{n!s}")  # tpyc: ok
    # repr text: `<E.MEMBER: value>` for every flavor
    print("free", repr(c), repr(n), repr(m), f"{c!r}", f"{n!r}")  # tpyc: ok
    # container elements render repr
    print("free", [c], [n], {c: 1}, (c, n))  # tpyc: ok
    # a spec: Enum pads its str() text with no warning, IntEnum formats its int
    print("free", f"[{c:>10}]", f"[{m:>6}]")  # tpyc: ok
    print("free", f"[{n:>10}]", f"[{n:03}]", f"[{n:d}]")  # tpyc: ok
    print("free", f"{get_n()}", repr(get_n()))


def gen() -> Iterator[str]:
    c = Color.GREEN
    yield f"{c}"  # tpyc: ok
    yield repr(Num.TWO)


def payload(oc: Color | None, on: Num | None) -> None:
    # Optional payload: as the bare enum, None when empty
    print("optional", oc, on, f"{oc}|{on}", f"{oc!r}|{on!r}")  # tpyc: ok


def match_arm(c: Color) -> None:
    match c:
        case Color.RED:
            print("match", f"{c}", repr(c))  # tpyc: ok
        case _:
            print("match", c)


def default_text() -> None:
    p = Plain()
    # print() writes the module-qualified default text, the same as repr()
    print("default", printed(p) == repr(p) + "\n",  # tpyc: ok
          printed(p).startswith("<__main__.Plain object at 0x"))
    # repr() is an owned str for bool
    t = repr(True)  # tpyc: ok
    u = repr(False)
    print("default", t, u)


def alias(j: J) -> None:
    # recursive alias: a str member is unquoted under str(), quoted under repr
    print("alias", j, f"{j}", f"{j!r}")  # tpyc: ok

def union_member(u: Num | str, v: Color | int32) -> None:
    # an enum held by a plain union renders as the bare enum
    print("union", f"{u}", f"{u!r}", f"{v}", f"{v!r}")  # tpyc: ok
    print("union", u, v)  # tpyc: ok


def nested() -> None:
    k = Msg.Kind.TEXT
    # a nested enum's text names the enum, not its owner
    print("nested", k, repr(k), f"{k}")  # tpyc: ok


def small_value() -> None:
    s = Small.A
    # an 8-bit underlying value prints as a number, not a char
    print("small", repr(s), [s], f"{s!r}")  # tpyc: ok


def imported() -> None:
    q = Q()
    # the default text names the defining module
    print("imported", printed_any(q) == repr(q) + "\n",  # tpyc: ok
          repr(q).startswith("<other.Q object at 0x"))


def main() -> None:
    free_function()
    Holder().show()
    for s in gen():
        print("generator", s)
    print("comprehension", [f"{e}" for e in [Num.ONE, Num.TWO]], [repr(e) for e in [Color.RED]])  # tpyc: ok
    oc: Color | None = Color.RED
    on: Num | None = Num.TWO
    payload(oc, on)
    oc = None
    on = None
    payload(oc, on)
    match_arm(Color.RED)
    match_arm(Color.GREEN)
    default_text()
    alias("hi")
    alias(["a", ["b"]])
    union_member(Num.ONE, Color.RED)
    union_member("s", 7)
    nested()
    imported()
    small_value()


main()
