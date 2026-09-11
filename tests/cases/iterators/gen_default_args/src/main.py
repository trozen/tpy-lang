# A generator factory with default arg values is callable with the arg omitted
# -- across the simple-peephole, resumable, generic, method, and Final paths.
# The resumable METHOD sections are the reproducer: a resumable method's
# canonical declaration is its in-class one, and the frame ctor is a second
# C++ callee, so both must carry the default.
from typing import Iterator, Iterable, Final
from enum import Enum
from tpy import Int32, Own


DEFAULT_STOP: Final[Int32] = 4
WIDTH: Final[Int32] = 7


class Mode(Enum):
    A = 1
    B = 2


class Rec:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


# Simple generator (single yield in a tail while-loop), two literal defaults.
def upto(stop: Int32 = 3, step: Int32 = 1) -> Iterator[Int32]:
    i: Int32 = 0
    while i < stop:
        yield i
        i += step


# Default referencing a module-level Final constant.
def upto_final(stop: Int32 = DEFAULT_STOP) -> Iterator[Int32]:
    i: Int32 = 0
    while i < stop:
        yield i
        i += 1


# Resumable generator (two yields), one default.
def bounded(limit: Int32 = 2) -> Iterator[Int32]:
    yield 0
    i: Int32 = 1
    while i <= limit:
        yield i
        i += 1


# Generic generator (protocol param) with a default -- the proto-param
# default-threading path, resumable via the break.
def head[T](it: Iterable[T], n: Int32 = 2) -> Iterator[T]:
    c: Int32 = 0
    for x in it:
        if c >= n:
            break
        yield x
        c += 1


class Box:
    base: Int32

    def __init__(self) -> None:
        self.base = 0

    # Simple generator METHOD with a default (the record_name peephole path).
    def upto_m(self, stop: Int32 = 2) -> Iterator[Int32]:
        i: Int32 = 0
        while i < stop:
            yield i
            i += 1

    # Resumable generator METHOD (two yields) -- the reproducer.
    def bounded_m(self, limit: Int32 = 2) -> Iterator[Int32]:
        yield self.base
        i: Int32 = 1
        while i <= limit:
            yield self.base + i
            i += 1

    # Every default shape at the resumable-method position, in one signature.
    def shapes(self, tag: str = "t", flag: bool = True, ratio: float = 0.5,
               m: Mode = Mode.B, r: Rec | None = None, w: Int32 = Int32(3),
               neg: Int32 = -1, f: Int32 = WIDTH) -> Iterator[Int32]:
        yield self.base
        print("shapes", tag, flag, ratio, m == Mode.B,
              -1 if r is None else r.v, w, neg, f)
        yield self.base + 1


class Box2[T]:
    items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self.items = items

    # Resumable generator method on a GENERIC class -- the monomorphic twin.
    def take(self, n: Int32 = 2) -> Iterator[T]:
        c: Int32 = 0
        for x in self.items:
            if c >= n:
                break
            yield x
            c += 1
        yield self.items[0]


def main() -> None:
    for v in upto():
        print(v)
    print("--")
    for v in upto(5):
        print(v)
    print("--")
    for v in upto(6, 2):
        print(v)
    print("--")
    for v in upto_final():
        print(v)
    print("--")
    for v in bounded():
        print(v)
    print("--")
    for v in bounded(1):
        print(v)
    print("--")
    nums: list[Int32] = [10, 20, 30, 40]
    for v in head(nums):
        print(v)
    print("--")
    for v in head(nums, 3):
        print(v)
    print("--")
    b = Box()
    for v in b.upto_m():
        print(v)
    print("--")
    for v in b.upto_m(3):
        print(v)

    # resumable generator METHOD, default omitted
    print("--")
    for v in b.bounded_m():                      # tpyc: ok
        print("meth", v)
    # resumable generator METHOD, explicit argument beats the default
    for v in b.bounded_m(1):                     # tpyc: ok
        print("meth-explicit", v)

    # resumable generator method on a generic class, default omitted
    g = Box2([10, 20, 30, 40])
    for v in g.take():                           # tpyc: ok
        print("gmeth", v)
    # generic-class method, explicit argument beats the default
    for v in g.take(3):                          # tpyc: ok
        print("gmeth-explicit", v)

    # default shapes on a resumable method, all omitted
    for v in b.shapes():                         # tpyc: ok
        print("shapes-v", v)
    # each shape overridden one at a time
    for v in b.shapes("z"):                      # tpyc: ok
        print("shapes-v", v)
    for v in b.shapes(flag=False):               # tpyc: ok
        print("shapes-v", v)
    for v in b.shapes(ratio=1.5):                # tpyc: ok
        print("shapes-v", v)
    for v in b.shapes(m=Mode.A):                 # tpyc: ok
        print("shapes-v", v)
    rec = Rec(9)
    for v in b.shapes(r=rec):                    # tpyc: ok
        print("shapes-v", v)
    for v in b.shapes(w=8):                      # tpyc: ok
        print("shapes-v", v)
    for v in b.shapes(neg=-5):                   # tpyc: ok
        print("shapes-v", v)
    for v in b.shapes(f=99):                     # tpyc: ok
        print("shapes-v", v)


main()
