# A call on a TYPE (constructor, builtin conversion) as a bare statement evaluates its
# REAL argument in every body position: `T(name);` in C++ statement position declares `name`.
from dataclasses import dataclass
from enum import Enum
from typing import Iterator
import tpy
import shapes
from tpy import Own, String, inline, int32


class Color(Enum):
    RED = 1


class Tick:
    def __init__(self, n: int32 = 0) -> None:
        self.n = n
        print("  tick", n)


class Wrap:
    def __init__(self, t: Tick) -> None:
        print("  wrap", t.n)


class Cell[T]:
    def __init__(self, v: T) -> None:
        print("  cell", v)


@dataclass
class Point:
    x: int32

    def __post_init__(self) -> None:
        print("  point", self.x)


class Outer:
    class Kind(Enum):
        A = 1

    class Inner:
        def __init__(self, n: int32 = 0) -> None:
            print("  inner", n)


class Scope:
    def __enter__(self) -> None:
        pass

    def __exit__(self, et, ev, tb) -> None:
        pass


class Holder:
    def __init__(self, n: int32) -> None:
        self.n = n
        # constructor body
        Tick(n)  # tpyc: ok

    def method(self) -> None:
        n = self.n
        if n > 0:
            # method, nested block
            Tick(n)  # tpyc: ok


def mk() -> Own[Tick]:
    return Tick(7)


def note(n: int32) -> int32:
    print("  note", n)
    return n


@inline
def build(v: int32) -> None:
    Tick(v)


def gen(n: int32) -> Iterator[int32]:
    # generator body
    Tick(n)  # tpyc: ok
    yield n


def free_fn(flag: bool) -> None:
    k: int32 = 3
    t = Tick(1)
    print("same-block:")
    # The name's own block: a redeclaration, were it a declaration.
    Tick(k)  # tpyc: ok
    Wrap(t)  # tpyc: ok
    Cell(k)  # tpyc: ok
    Cell[int32](k)  # tpyc: ok
    Point(k)  # tpyc: ok
    print("call-arg:")
    # A qualified call argument is the form only gcc read as an expression.
    Wrap(mk())  # tpyc: ok
    print("nested-block:")
    if flag:
        # A nested block: a declaration here shadows `k` and default-constructs.
        Tick(k)  # tpyc: ok
        Wrap(t)  # tpyc: ok
    print("other-args:")
    Tick()
    Tick(n=k)
    Tick(k + 1)
    Tick(t.n)


def spellings(flag: bool) -> None:
    # Every way a statement can name a type reaches the same lowered node.
    k: int32 = 3
    print("spellings:")
    if flag:
        # module-qualified record
        shapes.Tock(k)  # tpyc: ok
        # nested class
        Outer.Inner(k)  # tpyc: ok
        # the constructor arrives through an @inline expansion
        build(k)  # tpyc: ok
        # module-qualified builtin conversion
        tpy.int32(k)  # tpyc: ok
        print("  after", k)
    try:
        # enum-from-value: evaluated for its ValueError
        Color(k)  # tpyc: ok
    except ValueError:
        print("  bad color")
    try:
        # its nested twin takes the same verdict
        Outer.Kind(k)  # tpyc: ok
    except ValueError:
        print("  bad kind")


def positions(n: int32) -> None:
    print("closure:")

    def inner() -> None:
        if n > 0:
            Tick(n)  # tpyc: ok

    inner()
    print("with:")
    with Scope():
        Tick(n)  # tpyc: ok
    print("try-finally:")
    try:
        Tick(n)  # tpyc: ok
    finally:
        Tick(n)  # tpyc: ok
    print("match:")
    match n:
        case 4:
            Tick(n)  # tpyc: ok
        case _:
            pass
    print("exception:")
    msg = "unraised"
    if n > 0:
        # Constructed, never raised.
        ValueError(msg)  # tpyc: ok
    print("  ok", msg)


def conversions(flag: bool) -> None:
    print("conversions:")
    k: int32 = 3
    s = "abc"
    b = b"xy"
    digits = "12"
    if flag:
        # A builtin conversion is a call on a type too: a declaration would
        # shadow the name with an EMPTY value for the reads below it.
        str(s)  # tpyc: ok
        String(s)  # tpyc: ok
        bytes(b)  # tpyc: ok
        bytearray(b)  # tpyc: ok
        int32(k)  # tpyc: ok
        int(k)  # tpyc: ok
        float(k)  # tpyc: ok
        bool(k)  # tpyc: ok
        # from a string the conversion is a parse function, not a template
        int(digits)  # tpyc: ok
        float(digits)  # tpyc: ok
        # The discarded operand still evaluates. `int32` of an int32 is the
        # identity, so its cast folds away and only the call is left to wrap.
        int32(note(k))  # tpyc: ok
        float(note(k))  # tpyc: ok
        print("  after", s, len(b), k, digits)


def __init__(n: int32) -> int32:
    print("  free init", n)
    return n


def calls_stay_bare(n: int32) -> None:
    # Function and method calls are not functional casts: the bare statement stays.
    print("plain-calls:")
    note(n)
    Holder(n).method()
    # A free FUNCTION of this name is not a type's initializer.
    __init__(n)


def main() -> None:
    free_fn(True)
    spellings(True)
    print("ctor+method:")
    Holder(5).method()
    print("generator:")
    for v in gen(6):
        print("  yielded", v)
    positions(4)
    conversions(True)
    calls_stay_bare(8)


main()
g: int32 = 9
print("module-level:")
# Un-nested at module level: a declaration would shadow the GLOBAL inside the init function.
Tick(g)  # tpyc: ok
if g > 0:
    Tick(g)  # tpyc: ok
