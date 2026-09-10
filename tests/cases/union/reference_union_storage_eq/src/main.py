# A REFERENCE union at a storage position (a container element) compares BY
# VALUE across alternatives, as CPython does: `Dog | Int32 | Float64` holding 1
# equals one holding 1.0. Each section names the position it covers; the
# container compares are the subject lines. A union of two records that define
# no `__eq__` is NOT a section: it stays a toolchain-level refusal with no TPy
# location (BUGS.md#container-compare-record-without-eq).
import asyncio
from typing import Iterator

from tpy import Int32, ReturnException, error_return


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
        # under CPython, which routes the cross-alternative pair here where TPy
        # answers False without calling the dunder.
        if not isinstance(other, Dog):
            return False
        return self.n == other.n


class Cat:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: "Cat") -> bool:
        if not isinstance(other, Cat):
            return False
        return self.n == other.n


type Pet = Dog | Cat
# A record beside numeric alternatives: the pack whose cross-alternative
# answer the bare variant got wrong. `float` rather than the `Float64`
# spelling of the same type, which an alias body rejects
# (BUGS.md#imported-alias-member-in-alias-body).
type Mixed = Dog | Int32 | float

MOD_A: list[Mixed] = [1]
MOD_B: list[Mixed] = [1.0]
module_eq = MOD_A == MOD_B  # module-level statement  # tpyc: ok


def mixed_list_eq(xs: list[Mixed], ys: list[Mixed]) -> bool:  # free function
    return xs == ys  # tpyc: ok


def pet_list_eq(xs: list[Pet], ys: list[Pet]) -> bool:  # free function
    return xs == ys  # tpyc: ok


class Kennel:
    flag: bool

    def __init__(self, xs: list[Mixed], ys: list[Mixed]) -> None:
        self.flag = xs == ys  # constructor  # tpyc: ok

    def dict_eq(self, a: dict[str, Mixed], b: dict[str, Mixed]) -> bool:  # method
        return a == b  # tpyc: ok

    def pet_dict_eq(self, a: dict[str, Pet], b: dict[str, Pet]) -> bool:  # method
        return a == b  # tpyc: ok


def gen_eq(xs: list[Mixed], ys: list[Mixed]) -> Iterator[bool]:  # generator
    yield xs == ys  # tpyc: ok


def in_closure(xs: list[Mixed], ys: list[Mixed]) -> bool:  # closure
    def inner() -> bool:
        return xs == ys  # tpyc: ok

    return inner()


async def in_async(xs: list[Mixed], ys: list[Mixed]) -> bool:  # async
    return xs == ys  # tpyc: ok


def in_with(xs: list[Mixed], ys: list[Mixed]) -> bool:  # with body
    with Guard():
        return xs == ys  # tpyc: ok


def in_try(xs: list[Mixed], ys: list[Mixed]) -> bool:  # try/finally
    try:
        return xs == ys  # tpyc: ok
    finally:
        pass


@error_return(Boom)
def in_error_return(xs: list[Mixed], ys: list[Mixed]) -> bool:  # @error_return
    return xs == ys  # tpyc: ok


def in_match(xs: list[Mixed], ys: list[Mixed]) -> bool:  # match arm
    tag: Int32 = 1
    match tag:
        case 1:
            return xs == ys  # tpyc: ok
        case _:
            return False


# The comprehension binds its result to a local: in a RETURN expression it
# rejects at the `expr.list_comp` reject tag for any element type, union or
# not -- a plain `[e + 1 for e in xs]` over `list[Int32]` rejects the same
# way -- so the local is what reaches the comprehension body.
def in_comprehension(xs: list[Mixed], ys: list[Mixed]) -> Int32:  # comprehension
    flags = [xs == ys for _ in [1, 2]]  # tpyc: ok
    return len(flags) if flags[0] else 0


def nullable_eq(xs: list[Dog | Cat | None],
                ys: list[Dog | Cat | None]) -> bool:  # nullable alternatives
    return xs == ys  # tpyc: ok


# A union FIELD is a storage slot too. The store COPIES the record into the
# slot and says so, which is why a storage position has no identity answer to
# give: the slot is not the object CPython would have aliased.
class Crate:
    pet: Pet

    def __init__(self, pet: Pet) -> None:
        self.pet = pet  # tpyc: warning(/copies Pet into field/)


def crate_pet_n(c: Crate) -> Int32:
    p = c.pet
    if isinstance(p, Dog):
        return p.n
    return -1


# The union element is BORROWED out of the container, so a mutation through it
# is visible in the container -- if the read had copied, the bump would be
# lost.
def bump_first(xs: list[Pet]) -> None:
    e = xs[0]
    if isinstance(e, Dog):
        e.n += 1


def first_n(xs: list[Pet]) -> Int32:
    e = xs[0]
    if isinstance(e, Dog):
        return e.n
    return -1


async def amain(xs: list[Mixed], ys: list[Mixed]) -> None:
    print("async", await in_async(xs, ys))


def main() -> None:
    mi: list[Mixed] = [1]
    mf: list[Mixed] = [1.0]
    m2: list[Mixed] = [2]
    print("module", module_eq)
    print("free_fn", mixed_list_eq(mi, mf), mixed_list_eq(mi, m2))
    print("ctor", Kennel(mi, mf).flag)
    for g in gen_eq(mi, mf):
        print("generator", g)
    print("closure", in_closure(mi, mf))
    print("with", in_with(mi, mf))
    print("try", in_try(mi, mf))
    try:
        print("error_return", in_error_return(mi, mf))
    except Boom:
        print("error_return boom")
    print("match", in_match(mi, mf))
    print("comprehension", in_comprehension(mi, mf))
    asyncio.run(amain(mi, mf))

    dm1: dict[str, Mixed] = {"k": 1}
    dm2: dict[str, Mixed] = {"k": 1.0}
    dm3: dict[str, Mixed] = {"k": 2}
    k = Kennel(mi, mi)
    print("dict value", k.dict_eq(dm1, dm2), k.dict_eq(dm1, dm3))

    d1: list[Pet] = [Dog(1)]
    d2: list[Pet] = [Dog(1)]
    d3: list[Pet] = [Dog(2)]
    c1: list[Pet] = [Cat(1)]
    print("record same alternative", pet_list_eq(d1, d2), pet_list_eq(d1, d3))
    print("record cross alternative", pet_list_eq(d1, c1))
    pd1: dict[str, Pet] = {"k": Dog(3)}
    pd2: dict[str, Pet] = {"k": Dog(3)}
    pd3: dict[str, Pet] = {"k": Cat(3)}
    # Same alternative, DIFFERING value -- the dunder is reached and answers
    # False, which the cross-alternative pair below never gets to do.
    pd4: dict[str, Pet] = {"k": Dog(4)}
    print("record dict", k.pet_dict_eq(pd1, pd2), k.pet_dict_eq(pd1, pd4),
          k.pet_dict_eq(pd1, pd3))

    nn1: list[Dog | Cat | None] = [None]
    nn2: list[Dog | Cat | None] = [None]
    nd: list[Dog | Cat | None] = [Dog(1)]
    print("nullable", nullable_eq(nn1, nn2), nullable_eq(nn1, nd))

    print("field", crate_pet_n(Crate(Dog(7))))

    bump_first(d1)
    print("mutate through element", first_n(d1), pet_list_eq(d1, d2))


main()
