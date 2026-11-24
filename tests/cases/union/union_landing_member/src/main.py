# A non-union value lands in the union member sema recorded at the place
# receiving it, converted as its scalar twin `x: M = value` would be. Each
# section prints an isinstance test CPython answers alike: the member
# itself where the value already has that type, else a member it must not
# have landed in.
from typing import Iterator
from tpy import int16, int32, int64, uint8, uint32, uint64
from part_containers import main as containers_main
from part_records import main as records_main
from part_defaults import main as defaults_main
from part_positions import main as positions_main
from part_shapes import main as shapes_main
from part_frames import main as frames_main
from part_operators import main as operators_main
from part_operands import main as operands_main
from part_mixed import A, main as mixed_main
from part_finally import FBase, FSub, main as finally_main


def show(tag: str, v: int64 | uint64) -> None:
    print(tag, v, isinstance(v, uint64))


# local: a uint32 lands in int64, the scalar twin's widening
def local_widen(c: uint32) -> None:
    v: int64 | uint64 = c  # tpyc: ok
    show("local", v)


# local: a checked `int` lands in int64 through int64's range check
def checked(n: int) -> None:
    v: int64 | str = n  # tpyc: ok
    print("checked", v, isinstance(v, str))


# local: a literal lands in the first narrow member holding it
def literal_narrow() -> None:
    v: int16 | uint8 = 5  # tpyc: ok
    print("literal", v, isinstance(v, uint8))


def takes(tag: str, x: int32 | int64) -> None:
    print(tag, x, isinstance(x, int64))


# argument and local: an exact fixed-width value keeps its own member,
# a zero-argument constructor's too
def exact_member() -> None:
    takes("exact-arg", int64(5))  # tpyc: ok
    w: int32 | int64 = int64(5)  # tpyc: ok
    takes("exact-local", w)
    takes("zero-arg", int64())  # tpyc: ok
    z: int32 | int64 = int64()  # tpyc: ok
    takes("zero-local", z)
    n: int16 | uint8 = int16()  # tpyc: ok
    print("zero-narrow", n, isinstance(n, uint8))


# reassignment (a walrus into a value union is
# BUGS.md#value-union-walrus-rejects)
def rebind(c: uint32) -> None:
    v: int64 | uint64 = uint64(1)
    v = c  # tpyc: ok
    show("rebind", v)


# return slot
def ret(c: uint32) -> int64 | uint64:
    return c  # tpyc: ok


# generator yield slot
def gen(c: uint32) -> Iterator[int64 | uint64]:
    yield c  # tpyc: ok


# pending number settled into a union: the literal-seeded local is int32
def pending() -> None:
    n = 3
    v: int32 | str = n  # tpyc: ok
    print("pending", v, isinstance(v, str))


# module-level declaration: a fixed width beside a record lands in its own
# member, as in the all-scalar twin; an exact record member is unchanged
G64: A | int32 | int64 = int64(5)  # tpyc: ok
G16: A | int16 | int64 = int16(5)  # tpyc: ok
GU32: A | uint32 | int64 = uint32(5)  # tpyc: ok
GV: int32 | int64 = int64(5)  # tpyc: ok
GA: A | int32 = A(1)  # tpyc: ok
# module-level declaration: a subclass object lands as its base, as the
# twin `GB: FBase = FSub(5)` upcasts it (not FBase's own constructor)
GSUB: FBase | int32 = FSub(5)  # tpyc: warning(/narrows 'FSub'/)


if isinstance(G64, int64):
    print("global-mixed-int64")
if isinstance(G16, int16):
    print("global-mixed-int16")
if isinstance(GU32, uint32):
    print("global-mixed-uint32")
if isinstance(GV, int64):
    print("global-twin-int64")
if isinstance(GA, A):
    print("global-record")
if isinstance(GSUB, FBase):
    GSUB.n += 1
    print("global-subclass", GSUB.n)


def main() -> None:
    local_widen(3)
    checked(7)
    literal_narrow()
    exact_member()
    rebind(4)
    show("return", ret(9))
    for g in gen(8):
        show("yield", g)
    pending()
    positions_main()
    shapes_main()
    frames_main()
    containers_main()
    records_main()
    defaults_main()
    operators_main()
    operands_main()
    mixed_main()
    finally_main()


main()
