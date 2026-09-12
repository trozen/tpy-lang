# Module-init writes of non-value globals, one per SOURCE shape the pointer-slot
# arm accepts: a field lvalue (the slot points at the field), an Optional-record
# field (the storage member lifts to the pointer, both as a first write and as a
# write after a slot-allocating one), a subclass rvalue into an annotated base
# slot, and a union slot from a record rvalue or a list literal.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Line:
    a: Point

    def __init__(self) -> None:
        self.a = Point(0)


class Holder:
    xs: list[int32]
    inner: Point
    value: Point | None

    def __init__(self) -> None:
        self.xs = [1, 2]
        self.inner = Point(3)
        self.value = Point(4)


class Base:
    n: int32

    def __init__(self) -> None:
        self.n = 1


class Child(Base):
    extra: int32

    def __init__(self) -> None:
        self.n = 2
        self.extra = 9


h: Holder = Holder()
# A container field and a record field: each global aliases the field.
ys: list[int32] = h.xs  # tpyc: ok
p: Point = h.inner  # tpyc: ok
# An Optional[record] field is storage form, so the slot lifts it.
g: Point | None = h.value  # tpyc: ok
# A subclass rvalue into a base-annotated slot: the warned upcast.
b: Base = Child()  # tpyc: warning(/upcast narrows .Child. to .Base./)
# A value-variant slot from a record rvalue and from a list literal.
u: Point | Line = Point(5)  # tpyc: ok
v: list[int32] | int32 = [6, 7]  # tpyc: ok
# A first write allocates a static slot; the later Optional-field write must
# point at the OWNER's storage rather than reseat through that slot.
q: Point | None = Point(8)  # tpyc: ok
q = h.value  # tpyc: ok

ys.append(8)
p.x += 10
if g is not None:
    g.x += 100
if q is not None:
    q.x += 1000


# Reads through the owner prove the globals alias the fields rather than
# copying them.
print(len(h.xs), h.inner.x)
if h.value is not None:
    print(h.value.x)
print(b.n)
if isinstance(u, Point):
    print(u.x)
if isinstance(v, list):
    print(len(v))
