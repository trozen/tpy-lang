# MIR pins for inherited records: fields and methods declared on a base record,
# used through a subclass receiver -- summaries, writes, return origins and
# storage certificates keyed by the DECLARING owner; kept refusals beside them.
from typing import Protocol
from tpy import int32, StrView, dynamic, readonly


class Base:
    n: int32
    name: str
    items: list[int32]

    # base constructor: scalar, owned-leaf and empty container fields
    def __init__(self, name: str) -> None:  # tpyc: mir(covered)
        self.n = 0
        self.name = name
        self.items = []

    # base method: writes Base::n whatever the receiver's static type
    def bump(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.n += 1

    # base method: grows an inherited container field
    def push(self, v: int32) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.items.append(v)  # tpyc: mir_write(self.items[structure])

    # base method: replaces an inherited owned-leaf field
    def rename(self, s: str) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.name = s  # tpyc: mir_write(self.name)

    @readonly
    def total(self) -> int32:  # tpyc: mir(covered) mir_summary(known)
        t = 0
        for v in self.items:
            t += v
        return t

    # base method: a return origin inside the receiver (a container field)
    def view_items(self) -> list[int32]:  # tpyc: mir(covered) mir_summary(known)
        return self.items

    # base method: a view of an owned-leaf field
    @readonly
    def label(self) -> StrView:  # tpyc: mir(covered) mir_summary(known)
        return self.name


class Sub(Base):
    k: int32

    # subclass constructor: the base initializer chained, then an own field
    def __init__(self, name: str, k: int32) -> None:  # tpyc: mir(covered)
        super().__init__(name)
        self.k = k

    # subclass method: an inherited field and an own field written
    def bump_twice(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.n += 2
        self.k += 1

    # subclass method: an inherited container field grown from an own field
    def push_k(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.items.append(self.k)  # tpyc: mir_write(self.items[structure])

    # subclass readonly method: an inherited container field read
    @readonly
    def first(self) -> int32:  # tpyc: mir(covered) mir_summary(known)
        return self.items[0]

    # kept refusal: a super() call carries no receiver (C++ `this->Base::bump()`)
    def bump_super(self) -> None:  # tpyc: mir(uncovered /^call needs resolved ordinary callee$/)
        super().bump()


class Deep(Sub):
    d: int32

    # three-level chain: the grandparent's fields through two base initializers
    def __init__(self, name: str) -> None:  # tpyc: mir(covered)
        super().__init__(name, 1)
        self.d = 3

    def bump_deep(self) -> None:  # tpyc: mir(covered) mir_summary(known)
        self.n += self.d


class Animal:
    legs: int32

    def __init__(self, legs: int32) -> None:
        self.legs = legs

    def add_leg(self) -> None:
        self.legs += 1


# inherited constructor: no own fields, one struct base
class Dog(Animal):
    pass


class A:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


class B:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


# two struct bases, each initialized explicitly
class AB(A, B):
    def __init__(self, a: int32, b: int32) -> None:  # tpyc: mir(covered)
        A.__init__(self, a)
        B.__init__(self, b)

    def total(self) -> int32:  # tpyc: mir(covered) mir_summary(known)
        return self.a + self.b


@dynamic
class Shape(Protocol):
    def grow(self) -> None: ...


class VBase(Shape):
    size: int32

    def __init__(self) -> None:
        self.size = 0

    def grow(self) -> None:
        self.size += 1


class VSub(VBase):
    def __init__(self) -> None:  # tpyc: mir(covered)
        super().__init__()

    def grow(self) -> None:  # tpyc: mir(covered)
        self.size += 2


# free function: a subclass parameter, base methods applied through it
def use_sub(s: Sub) -> int32:  # tpyc: mir(covered)
    s.bump()
    s.push(3)  # tpyc: mir_write(s.items[structure])
    s.bump_twice()
    s.push_k()
    return s.total() + s.first() + s.k


# free function: a return origin through an inherited field, alias kept across a
# sibling write and written through (the record sees the element the alias adds)
def alias_inherited(s: Sub) -> int32:  # tpyc: mir(covered)
    xs = s.view_items()  # tpyc: mir_borrows(xs, s.items)
    s.bump()
    xs.append(9)
    return s.total()


# free function: an inherited owned-leaf field replaced while a view of it is held.
# The local is annotated so it holds a view (unannotated it would own a copy); that
# the view reads the new content afterwards is
# BUGS.md#explicit-view-local-source-mutation-unguarded, hidden here by len().
def view_then_rename(s: Sub) -> int32:  # tpyc: mir(conflict /replacement/)
    t: StrView = s.label()  # tpyc: mir_borrows(t, s.name)
    s.rename("zz")  # tpyc: mir_write(s.name)
    return len(t)


# free function: a subclass bound at its base's type at a call
def use_base(b: Base) -> int32:
    b.bump()
    return b.total()


def through_base(s: Sub) -> int32:  # tpyc: mir(covered)
    return use_base(s)


# free function: a readonly subclass parameter
@readonly
def read_sub(s: readonly[Sub]) -> int32:  # tpyc: mir(covered)
    return s.total() + s.k


# free function: a grandchild receiver
def use_deep(d: Deep) -> int32:  # tpyc: mir(covered)
    d.bump()
    d.bump_deep()
    return d.n


# free function: an inherited-constructor record
def use_dog(d: Dog) -> int32:  # tpyc: mir(covered)
    d.add_leg()
    return d.legs


# free function: a two-base record
def use_ab(ab: AB) -> int32:  # tpyc: mir(covered)
    return ab.total()


# free function: a subclass constructed in the caller (scalar fields only)
def make_dog() -> int32:  # tpyc: mir(covered)
    d = Dog(4)
    d.add_leg()
    return d.legs


# free function: subclass records as container elements (leaf fields only)
def elements(ds: list[Dog]) -> int32:  # tpyc: mir(covered)
    t = 0
    for d in ds:
        d.add_leg()
        t += d.legs
    return t


# kept refusal: the caller of a method whose body refuses (the super() call) has no summary
def call_super(s: Sub) -> int32:  # tpyc: mir(uncovered /^call needs finalized known summary$/)
    s.bump_super()
    return s.n


# kept refusal: a virtual owner -- the static type does not name the body that runs
def call_virtual(v: VBase) -> None:  # tpyc: mir(uncovered /^unsupported expression type$/)
    v.grow()


# kept refusal: an upcast local binds a subclass at its base's type
def upcast_local(s: Sub) -> int32:  # tpyc: mir(uncovered /^unsupported metadata: cpp_local_representation$/)
    b: Base = s  # tpyc: warning(/upcast narrows/)
    b.bump()
    return b.n


# kept refusal: an Optional local binds a subclass at its base's type
def upcast_payload(s: Sub) -> int32:  # tpyc: mir(uncovered /^optional backing storage$/)
    cur: Base | None = s  # tpyc: warning(/upcast narrows/)
    if cur is not None:
        return cur.n
    return 0


def main() -> None:  # tpyc: mir(uncovered /^constructor container field$/)
    s = Sub("a", 5)
    print("use_sub", use_sub(s))
    print("alias", alias_inherited(s))
    print("view", view_then_rename(s))
    print("through", through_base(s))
    print("read", read_sub(s))
    d = Deep("d")
    print("deep", use_deep(d))
    print("dog", use_dog(Dog(4)), make_dog())
    print("elements", elements([Dog(1), Dog(2)]))
    print("ab", use_ab(AB(1, 2)))
    v = VSub()
    call_virtual(v)
    print("virtual", v.size)
    print("super", call_super(s))
    print("upcast", upcast_local(s))
    print("payload", upcast_payload(s))
    # the upcast bumped the Sub itself: read after the calls, not inside their print
    print("after", s.n)


main()
