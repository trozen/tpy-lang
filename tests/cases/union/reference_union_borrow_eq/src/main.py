# A REFERENCE union at a BORROW position compares through the POINTEE, as
# CPython does: its `__eq__` / `__ne__` when it declares one, IDENTITY when it
# declares neither -- the answer only a borrow form can give, because there
# the pointer is the object. Each section names the position it covers; the
# comparison lines are the subject lines. Module scope has no section: a
# module-level union global is not bound as the borrow form, so a compare
# there still rejects. Storage twin: `union/reference_union_storage_eq`.
import asyncio
from typing import Iterator

from tpy import Int32, ReturnException, error_return, nocopy, readonly


class Boom(Exception, ReturnException):
    pass


class Guard:
    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Dog:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: "Dog") -> bool:
        # Folds to True under TPy (`other` is typed Dog) and is a real check
        # under CPython, which routes the cross-alternative pair here where
        # TPy answers False without calling the dunder.
        if not isinstance(other, Dog):
            return False
        return self.n == other.n

    def __lt__(self, other: "Dog") -> bool:
        return self.n < other.n

    def __le__(self, other: "Dog") -> bool:
        return self.n <= other.n

    def __gt__(self, other: "Dog") -> bool:
        return self.n > other.n

    def __ge__(self, other: "Dog") -> bool:
        return self.n >= other.n


class Cat:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: "Cat") -> bool:
        if not isinstance(other, Cat):
            return False
        return self.n == other.n

    def __lt__(self, other: "Cat") -> bool:
        return self.n < other.n

    def __le__(self, other: "Cat") -> bool:
        return self.n <= other.n

    def __gt__(self, other: "Cat") -> bool:
        return self.n > other.n

    def __ge__(self, other: "Cat") -> bool:
        return self.n >= other.n


# `__ne__` INVERTED on purpose: CPython calls a declared `__ne__` instead of
# deriving one from `__eq__`, so both rows below disagree with the negation.
class Tag:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: "Tag") -> bool:
        if not isinstance(other, Tag):
            return False
        return self.n == other.n

    def __ne__(self, other: "Tag") -> bool:
        if not isinstance(other, Tag):
            return True
        return self.n == other.n


class Mark:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: "Mark") -> bool:
        if not isinstance(other, Mark):
            return False
        return self.n == other.n


# `__ne__` and NO `__eq__`: `!=` calls the dunder while `==` still falls back
# to identity, so the two operators answer from different rules on one pair.
class OnlyNe:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __ne__(self, other: "OnlyNe") -> bool:
        if not isinstance(other, OnlyNe):
            return True
        return self.n != other.n


# Neither alternative defines `__eq__`, so Python falls back to identity --
# the row the storage form cannot answer, because the slot it compares is a
# copy of the object rather than the object.
class Plain:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


class Other:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


@nocopy
class Locked:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: "Locked") -> bool:
        if not isinstance(other, Locked):
            return False
        return self.n == other.n


@nocopy
class Sealed:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: "Sealed") -> bool:
        if not isinstance(other, Sealed):
            return False
        return self.n == other.n


type Pet = Dog | Cat
type Labelled = Tag | Mark
type Lopsided = OnlyNe | Other
type Anon = Plain | Other
type Held = Locked | Sealed
# A record beside VALUE alternatives: the borrowed pointee leg goes through
# the same value leaf the storage form uses, so `1` and `1.0` are equal here
# too. `float` rather than the `Float64` spelling of the same type, which an
# alias body rejects (BUGS.md#imported-alias-member-in-alias-body).
type Mixed = Dog | Int32 | float


def eq(a: Pet, b: Pet) -> bool:  # free function param
    return a == b  # tpyc: ok


def ne(a: Pet, b: Pet) -> bool:  # free function param
    return a != b  # tpyc: ok


def order(a: Pet, b: Pet) -> str:  # free function param
    if a < b:  # tpyc: ok
        return "lt"
    if a > b:  # tpyc: ok
        return "gt"
    if a <= b and a >= b:  # tpyc: ok
        return "eq"
    return "?"


def dunder_ne(a: Labelled, b: Labelled) -> bool:  # a declared `__ne__`
    return a != b  # tpyc: ok


def only_ne(a: Lopsided, b: Lopsided) -> bool:  # `__ne__` without `__eq__`
    return a != b  # tpyc: ok


def only_ne_eq(a: Lopsided, b: Lopsided) -> bool:  # the same pair's `==`
    return a == b  # tpyc: ok


def opt_ne(a: Pet | None, b: Pet | None) -> bool:  # a None alternative
    return a != b  # tpyc: ok


# Both operands render `::tpy::Union<const Cat*, const Dog*>`: a
# `readonly[...]` annotation and an inferred non-mutating slot are one
# spelling, so the pair compares instead of rejecting as a mixed render.
def readonly_mixed(a: readonly[Pet], b: Pet) -> bool:  # readonly vs inferred
    return a == b  # tpyc: ok


def local_vs_param(xs: list[Pet], u: Pet) -> bool:  # const local vs param
    e = xs[0]
    return e == u  # tpyc: ok


def anon_eq(a: Anon, b: Anon) -> bool:  # identity fallback
    return a == b  # tpyc: ok


def anon_ne(a: Anon, b: Anon) -> bool:  # identity fallback, negated
    return a != b  # tpyc: ok


def held_eq(a: Held, b: Held) -> bool:  # @nocopy alternatives
    return a == b  # tpyc: ok


def mixed_eq(xs: list[Mixed]) -> None:  # borrowed value members
    a = xs[0]
    b = xs[1]
    c = xs[2]
    print("value members", a == b, a == c)  # tpyc: ok


class Shelter:
    tag: Int32
    flag: bool

    def __init__(self, tag: Int32, a: Pet, b: Pet) -> None:  # constructor
        self.tag = tag
        self.flag = a == b  # tpyc: ok

    def same(self, a: Pet, b: Pet) -> bool:  # method param
        return a == b  # tpyc: ok

    @readonly
    def same_ro(self, a: Pet, b: Pet) -> bool:  # @readonly method param
        return a == b  # tpyc: ok


def elem_eq(xs: list[Pet]) -> bool:  # local lifted out of a container
    a = xs[0]
    b = xs[1]
    return a == b  # tpyc: ok


# The comprehension binds its result to a local: in a RETURN expression it
# rejects at `expr.list_comp` for any element type. The loop VARIABLE is not
# an operand here -- iterating `list[Pet]` binds the storage element, which
# is a different C++ type from the borrowed parameter
# (BUGS.md#ref-union-loop-var-vs-borrow-compare).
def count_eq(a: Pet, b: Pet) -> Int32:  # comprehension
    flags = [a == b for _ in [1, 2]]  # tpyc: ok
    return len(flags) if flags[0] else 0


def gen_eq(a: Pet, b: Pet) -> Iterator[bool]:  # generator frame
    yield a == b  # tpyc: ok


def in_closure(a: Pet, b: Pet) -> bool:  # closure
    def inner() -> bool:
        return a == b  # tpyc: ok

    return inner()


async def in_async(a: Pet, b: Pet) -> bool:  # async
    return a == b  # tpyc: ok


def in_with(a: Pet, b: Pet) -> bool:  # with body
    with Guard():
        return a == b  # tpyc: ok


def in_try(a: Pet, b: Pet) -> bool:  # try/finally
    try:
        return a == b  # tpyc: ok
    finally:
        pass


@error_return(Boom)
def in_error_return(a: Pet, b: Pet) -> bool:  # @error_return
    return a == b  # tpyc: ok


def in_match(a: Pet, b: Pet) -> bool:  # match arm
    tag: Int32 = 1
    match tag:
        case 1:
            return a == b  # tpyc: ok
        case _:
            return False


# The parameter BORROWS, so the bump is visible in the caller's object -- a
# copy at the boundary would lose it and the compare afterwards would still
# say equal.
def bump(u: Pet) -> None:
    if isinstance(u, Dog):
        u.n += 1


async def amain(a: Pet, b: Pet) -> None:
    print("async", await in_async(a, b))


def main() -> None:
    d1 = Dog(1)
    d2 = Dog(1)
    d3 = Dog(2)
    c1 = Cat(1)
    print("free_fn eq", eq(d1, d2), eq(d1, d3), eq(d1, c1))
    print("free_fn ne", ne(d1, d2), ne(d1, c1))
    print("ordering", order(d1, d3), order(d3, d1), order(d1, d2))

    pd1: Pet = d1
    pd2: Pet = d2
    pc1: Pet = c1
    s = Shelter(1, pd1, pd2)
    print("constructor", s.flag, Shelter(1, pd1, pc1).flag)
    print("method", s.same(pd1, pd2), s.same(pd1, pc1))
    print("readonly method", s.same_ro(pd1, pd2), s.same_ro(pd1, pc1))

    xs: list[Pet] = [Dog(1), Dog(1)]
    ys: list[Pet] = [Dog(1), Cat(1)]
    print("local", elem_eq(xs), elem_eq(ys))

    print("comprehension", count_eq(d1, d2))
    for g in gen_eq(d1, d2):
        print("generator", g)
    print("closure", in_closure(d1, d2))
    print("with", in_with(d1, d2))
    print("try", in_try(d1, d2))
    try:
        print("error_return", in_error_return(d1, d2))
    except Boom:
        print("error_return boom")
    print("match", in_match(d1, d2))
    asyncio.run(amain(d1, d2))

    # Identity: the same object equals itself and differs from a distinct
    # object with the same fields, because neither alternative defines
    # `__eq__`.
    p1 = Plain(1)
    p2 = Plain(1)
    o1 = Other(1)
    print("identity", anon_eq(p1, p1), anon_eq(p1, p2), anon_eq(p1, o1))
    print("identity ne", anon_ne(p1, p1), anon_ne(p1, p2))

    # The declared `__ne__` answers both rows, and disagrees with the
    # negation of `__eq__` on both.
    t1 = Tag(1)
    t2 = Tag(1)
    t3 = Tag(2)
    m1 = Mark(1)
    print("custom_ne", dunder_ne(t1, t2), dunder_ne(t1, t3),
          dunder_ne(t1, m1))

    # `!=` reaches the dunder, `==` reaches identity, on the same two
    # objects: distinct-but-equal answers False for `!=` and False for `==`.
    n1 = OnlyNe(1)
    n2 = OnlyNe(1)
    print("only_ne", only_ne(n1, n2), only_ne(n1, n1),
          only_ne_eq(n1, n2), only_ne_eq(n1, n1))

    print("readonly mixed", readonly_mixed(d1, d2), readonly_mixed(d1, c1))
    print("const local", local_vs_param(xs, d1), local_vs_param(ys, c1))

    op_none: Pet | None = None
    op_dog: Pet | None = d2
    print("none ne", opt_ne(op_none, op_dog), opt_ne(op_none, op_none))

    l1 = Locked(1)
    l2 = Locked(1)
    sl = Sealed(1)
    print("nocopy", held_eq(l1, l2), held_eq(l1, sl))

    mixed: list[Mixed] = [1, 1.0, Dog(1)]
    mixed_eq(mixed)

    bump(d1)
    print("mutate through borrow", d1.n, eq(d1, d2))


main()
