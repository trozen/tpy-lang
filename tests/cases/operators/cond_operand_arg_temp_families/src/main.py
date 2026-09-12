# One section per ARGUMENT-TEMP family that can materialize inside a
# conditionally evaluated operand (an `or` right operand here): the temp is
# built where the operand runs, never ahead of the guard.
# Every section runs twice -- SKIPPED first, then TAKEN -- and prints only
# from inside the argument's construction or the callee, so a temp hoisted
# ahead of the guard shows up as an extra output line under the skipped call.
# The sections whose argument derives from a live list pop from it in the
# always-evaluated part, so what the callee reports names the moment the
# temp was taken.
from typing import Iterator, Protocol
from tpy import Deref, int32, Own, auto_readonly, dynamic
from tplib import Box


@dynamic
class Shape(Protocol):
    def area(self) -> float: ...


class Circle(Shape):
    r: float

    def __init__(self, r: float) -> None:
        self.r = r

    def area(self) -> float:
        return 3.0 * self.r


class Neg:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


type Value = int | Neg | list[Value]

type Tree[T] = T | list[Tree[T]]


class Sink:
    seen: int32

    def __init__(self) -> None:
        self.seen = 0

    def take(self, tag: str, xs: list[int32] | None) -> bool:
        if xs is not None:
            self.seen = len(xs)
        print(tag, self.seen)
        return True


class Inner:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


class Outer:
    m: int32

    def __init__(self, i: Inner) -> None:
        # A MUTATED constructor parameter, so an `Inner(..)` rvalue argument
        # needs its own hoisted temp.
        i.bump()
        self.m = i.n


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        print("genrecv build", n)
        self.n = n

    def items(self) -> Iterator[int32]:
        i = 0
        while i < self.n:
            yield i
            i += 1


def sized(tag: str, xs: list[int32]) -> bool:
    print(tag, len(xs))
    return True


def shaped(tag: str, b: Box[Shape]) -> bool:
    print(tag, b.get().area())
    return True


def anyslot[T](tag: str, v: T) -> bool:
    print(tag, "took")
    return True


def listslot[T](tag: str, v: list[T]) -> bool:
    print(tag, len(v))
    return True


def show(tag: str, t: Value) -> int32:
    match t:
        case list() as xs:
            n = 0
            for c in xs:
                n += show(tag, c)
            return n
        case Neg() as g:
            print(tag, "neg")
            return -g.v
        case int():
            print(tag, "int")
            return 1


def leaf_count(tag: str, t: Tree[int32]) -> int32:
    match t:
        case list() as branches:
            total = 0
            for child in branches:
                total += leaf_count(tag, child)
            return total
        case _:
            print(tag, "leaf")
            return 1


def make_leaf() -> Own[Tree[int32]]:
    print("rucall build")
    return int32(7)


def held(tag: str, o: Outer) -> bool:
    print(tag, o.m)
    return True


def comprehension(src: list[int32], flag: bool) -> bool:
    # `or` RHS, comprehension into a container slot: the pop runs first, so
    # the comprehension must see the SHORTENED list.
    return flag or (src.pop() >= 0 and sized("comp", [v * 2 for v in src]))


def covariant(flag: bool) -> bool:
    # `or` RHS, covariant upcast of a local `Box[Circle]` into a `Box[Shape]`
    # slot -- the temp absorbs the converting move.
    bc = Box(Circle(5.0))
    return flag or shaped("cov", bc)


def generic_ref_slot(flag: bool) -> bool:
    # `or` RHS, a scalar literal at a bare type-parameter slot.
    return flag or anyslot("genref", 42)


def generic_container_literal(flag: bool) -> bool:
    # `or` RHS, a list literal at a `list[T]` slot of a generic function.
    return flag or listslot("gencont", [1, 2, 3])


def optptr_container_literal(s: Sink, flag: bool) -> bool:
    # `or` RHS, a list literal at a METHOD's pointer-repr `Optional[list]`
    # slot -- the temp is passed by address.
    return flag or s.take("optptr", [1, 2, 3])


def recursive_union_literal(flag: bool) -> int32:
    # `or` RHS, a list literal at a recursive-union wrapper slot.
    return int32(0) if flag else show("rulit", [1, 2, 3])


def ru_wrapper_literal(flag: bool) -> int32:
    # Ternary arm, a scalar literal at the same wrapper slot.
    return int32(0) if flag else show("ruscalar", 9)


def ru_wrapper_ctor(flag: bool) -> int32:
    # Ternary arm, a member-record ctor rvalue at the wrapper slot.
    return int32(0) if flag else show("ructor", Neg(3))


def ru_wrapper_call(flag: bool) -> int32:
    # Ternary arm, an `Own[Tree[int32]]`-returning call at the wrapper slot;
    # the build print says whether the skipped arm ran it.
    return int32(0) if flag else leaf_count("rucall", make_leaf())


def ctor_mut_rvalue(flag: bool) -> bool:
    # `or` RHS, a record rvalue at a MUTATED constructor slot.
    return flag or held("ctormut", Outer(Inner(3)))


def gen_recv_temp(flag: bool) -> bool:
    # `or` RHS, a GENERATOR method on a constructor rvalue: the frame borrows
    # the receiver, so the receiver itself hoists a temp inside the operand.
    return flag or sized("genrecv", [v for v in Counter(3).items()])


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Ref(Deref[Point]):
    _target: Point

    def __init__(self, target: Point) -> None:
        self._target = target  # tpyc: warning(/copies Point into field/)

    @auto_readonly
    def __deref__(self) -> Point:
        return self._target


def shown(tag: str, p: Point) -> bool:
    print(tag, p.x)
    return True


def bump(r: Ref) -> bool:
    r._target.x += 1
    return True


def deref_or_rhs(r: Ref, flag: bool) -> bool:
    # `or` RHS, deref coercion: a `__deref__` wrapper at a plain record slot
    # takes a VALUE copy of the pointee, and the mutation on the
    # always-evaluated left runs first, so the copy must report the BUMPED
    # value.
    return flag or (bump(r) and shown("deref", r))


def deref_ternary(r: Ref, flag: bool) -> bool:
    # Ternary arm: the same deref-coercion temp at the other conditional
    # position.
    return shown("dereftern", r) if not flag else False


def main() -> None:
    # Each result binds before it is printed: a callee that prints while the
    # print statement is mid-stream would interleave under TPy but not under
    # CPython, which is a different divergence than the one under test.
    a1 = comprehension([1, 2, 3], True)
    print("comp skipped", a1)
    a2 = comprehension([1, 2, 3], False)
    print("comp taken", a2)
    b1 = covariant(True)
    print("cov skipped", b1)
    b2 = covariant(False)
    print("cov taken", b2)
    c1 = generic_ref_slot(True)
    print("genref skipped", c1)
    c2 = generic_ref_slot(False)
    print("genref taken", c2)
    d1 = generic_container_literal(True)
    print("gencont skipped", d1)
    d2 = generic_container_literal(False)
    print("gencont taken", d2)
    s = Sink()
    e1 = optptr_container_literal(s, True)
    print("optptr skipped", e1)
    e2 = optptr_container_literal(s, False)
    print("optptr taken", e2)
    # The method wrote through the receiver, so the count survives the call.
    print("optptr seen", s.seen)
    f1 = recursive_union_literal(True)
    print("rulit skipped", f1)
    f2 = recursive_union_literal(False)
    print("rulit taken", f2)
    g1 = ru_wrapper_literal(True)
    print("ruscalar skipped", g1)
    g2 = ru_wrapper_literal(False)
    print("ruscalar taken", g2)
    h1 = ru_wrapper_ctor(True)
    print("ructor skipped", h1)
    h2 = ru_wrapper_ctor(False)
    print("ructor taken", h2)
    i1 = ru_wrapper_call(True)
    print("rucall skipped", i1)
    i2 = ru_wrapper_call(False)
    print("rucall taken", i2)
    j1 = ctor_mut_rvalue(True)
    print("ctormut skipped", j1)
    j2 = ctor_mut_rvalue(False)
    print("ctormut taken", j2)
    k1 = gen_recv_temp(True)
    print("genrecv skipped", k1)
    k2 = gen_recv_temp(False)
    print("genrecv taken", k2)
    m = Ref(Point(7))
    m1 = deref_or_rhs(m, True)
    print("deref skipped", m1)
    m2 = deref_or_rhs(m, False)
    print("deref taken", m2)
    n = Ref(Point(20))
    n1 = deref_ternary(n, True)
    print("dereftern skipped", n1)
    n2 = deref_ternary(n, False)
    print("dereftern taken", n2)


main()
