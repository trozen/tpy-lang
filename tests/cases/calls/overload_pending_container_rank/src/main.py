# The overload rank of an undecided container argument where the pick goes
# against what CPython's `dispatch` stand-in does (it tries concrete
# variants before generic ones and otherwise picks by declaration order):
# the least container widening wins although a widening candidate is
# declared first, and a generic that widens nothing beats a concrete one
# that widens. Each variant names itself; a winner that can write the list
# does, and the caller reads it back.
from typing import Iterable

from tpy import dispatch, int32, int64


# A typed floor: the list holds an int32, int64 widens less than int.
@dispatch
def floor_b(xs: list[int]) -> str:
    xs.append(7)
    return "int"


@dispatch
def floor_b(xs: list[int64]) -> str:
    xs.append(7)
    return "int64"


# The concrete candidate would widen the list; the declared view, declared
# second here, takes it as it is.
@dispatch
def concrete_first(xs: list[int64]) -> str:
    return "list64"


@dispatch
def concrete_first(xs: Iterable[int32]) -> str:
    return "iter32"


# The same in both scalar spellings: an unrelated scalar that converts must
# not change the container's pick.
@dispatch
def scalar32(n: int32, xs: list[int64]) -> str:
    return "list"


@dispatch
def scalar32(n: int32, xs: Iterable[int64]) -> str:
    return "Iterable"


@dispatch
def scalar_big(n: int, xs: list[int64]) -> str:
    return "list"


@dispatch
def scalar_big(n: int, xs: Iterable[int64]) -> str:
    return "Iterable"


# A list of int widens a held int32 more than a view of int64 converts.
@dispatch
def to_big(xs: list[int]) -> str:
    return "big"


@dispatch
def to_big(xs: Iterable[int64]) -> str:
    return "iter64"


# The generic instantiated at int32 widens nothing and is the more specific
# tier over the view; the concrete list[int64] widens.
@dispatch
def tiers(xs: list[int64]) -> str:
    xs.append(7)
    return "list[int64]"


@dispatch
def tiers[T](xs: list[T]) -> str:
    xs.append(xs[0])
    return "generic"


@dispatch
def tiers(xs: Iterable[int32]) -> str:
    return "Iterable[int32]"


def main() -> None:
    fb = [1]  # tpyc: type(list[int64])
    fb.append(int32(2))
    print("floor b:", floor_b(fb), fb)  # int64 over int, declared second
    cf = [1, 2]  # tpyc: type(Array[int32, 2])
    print("concrete first:", concrete_first(cf), cf)
    s1 = [1, 2]  # tpyc: type(Array[int32, 2])
    print("scalar int32:", scalar32(1, s1), s1)
    s2 = [1, 2]  # tpyc: type(Array[int32, 2])
    print("scalar int:", scalar_big(1, s2), s2)  # the scalar converts
    b1 = [1, 2]  # tpyc: type(Array[int32, 2])
    print("to big:", to_big(b1), b1)
    ti = [1, 2]  # tpyc: type(list[int32])
    print("tiers:", tiers(ti), ti)


main()
