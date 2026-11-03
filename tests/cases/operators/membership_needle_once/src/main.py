# `needle in it` over a user iterator with no `__contains__`: the needle is
# evaluated once, before `__iter__`, and every element is compared against
# that one value, at every position, as CPython does.
import asyncio
from enum import Enum
from typing import Callable, Iterator
from tpy import Own, int32


class Color(Enum):
    RED = 1
    BLUE = 3


class Nums:
    n: int32
    i: int32

    def __init__(self, n: int32) -> None:
        self.n = n
        self.i = 0

    def __iter__(self) -> "Nums":
        print("  iter")
        return self

    def __next__(self) -> int32:
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i


class Shades:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> "Shades":
        return self

    def __next__(self) -> Color:
        if self.i >= 2:
            raise StopIteration
        self.i += 1
        return Color.RED if self.i == 1 else Color.BLUE


class Moving:
    # `__next__` writes the field the needle reads.
    k: int32
    i: int32

    def __init__(self) -> None:
        self.k = 1
        self.i = 0

    def __iter__(self) -> "Moving":
        return self

    def __next__(self) -> int32:
        if self.i >= 2:
            raise StopIteration
        self.i += 1
        self.k = 5
        return 1


class Growing:
    # `__next__` appends to the list the needle reads an element of.
    xs: list[int]
    i: int32

    def __init__(self) -> None:
        self.xs = [2]
        self.i = 0

    def __iter__(self) -> "Growing":
        return self

    def __next__(self) -> int:
        if self.i >= 3:
            raise StopIteration
        self.i += 1
        self.xs.append(self.i)
        return 2


class Flags:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> "Flags":
        return self

    def __next__(self) -> bool:
        if self.i >= 2:
            raise StopIteration
        self.i += 1
        return self.i == 2


class BigNums:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> "BigNums":
        return self

    def __next__(self) -> int:
        if self.i >= 2:
            raise StopIteration
        self.i += 1
        return 9 if self.i == 2 else 1


g_needle = 1  # tpyc: type(int32)


class Rebinding:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> "Rebinding":
        global g_needle
        g_needle = 2
        return self

    def __next__(self) -> int32:
        if self.i >= 1:
            raise StopIteration
        self.i += 1
        return 1


def needle() -> int32:
    print("  needle")
    return 2


def color() -> Color:
    print("  color")
    return Color.BLUE


def make() -> Own[list[int]]:
    print("  make")
    return [9, 2]


def g_body(nn: Nums) -> Iterator[bool]:
    # generator frame
    yield needle() in nn  # tpyc: ok


async def a_body(nn: Nums) -> bool:
    # async frame
    return needle() not in nn  # tpyc: ok


def lam(nn: Nums) -> bool:
    # lambda body
    f: Callable[[Nums], bool] = lambda m: needle() in m  # tpyc: ok
    return f(nn)


def wcond(nn: Nums) -> int32:
    k = 0
    # while condition: one needle per test
    while needle() in nn:  # tpyc: ok
        k += 1
    return k


def compcond(nn: Nums) -> Own[list[int32]]:
    # comprehension `if`
    return [q for q in range(2) if needle() in nn]  # tpyc: ok


def ternary(nn: Nums, c: bool) -> bool:
    # ternary arm
    return (needle() in nn) if c else False  # tpyc: ok


def and_rhs(nn: Nums, c: bool) -> bool:
    # right of `and`
    return c and needle() in nn  # tpyc: ok


def main() -> None:
    print("plain:")
    nn = Nums(3)
    r_plain = needle() in nn  # tpyc: ok
    print("plain:", r_plain)
    print("not_in:")
    n2 = Nums(3)
    r_not_in = needle() not in n2  # tpyc: ok
    print("not_in:", r_not_in)
    print("empty:")
    e = Nums(0)
    r_empty = needle() in e  # tpyc: ok
    print("empty:", r_empty)
    print("enum:")
    sh = Shades()
    r_enum = color() in sh  # tpyc: ok
    print("enum:", r_enum)
    sh2 = Shades()
    print("enum_const:", Color.BLUE in sh2)  # tpyc: ok
    print("gen:")
    for b in g_body(Nums(3)):
        print("gen:", b)
    print("async:")
    r_async = asyncio.run(a_body(Nums(3)))
    print("async:", r_async)
    print("lambda:")
    r_lambda = lam(Nums(3))
    print("lambda:", r_lambda)
    print("while:")
    r_while = wcond(Nums(3))
    print("while:", r_while)
    print("comp:")
    r_comp = compcond(Nums(3))
    print("comp:", r_comp)
    print("ternary:")
    r_ternary = ternary(Nums(3), True)
    print("ternary:", r_ternary)
    print("and:")
    r_and = and_rhs(Nums(3), True)
    print("and:", r_and)
    a = Moving()
    # the iteration writes the field the needle read: the first value counts
    print("field:", a.k in a)  # tpyc: ok
    gr = Growing()
    # the iteration grows the list the needle is an element of
    r_element = gr.xs[0] in gr  # tpyc: ok
    print("element:", r_element, len(gr.xs))
    print("temporary:")
    bn = BigNums()
    # a BigInt needle off a temporary: make() once, its element copied
    r_temporary = make()[0] in bn  # tpyc: ok
    print("temporary:", r_temporary)
    bs = [True, False]
    fl = Flags()
    # a bool element needle: a bool local, never a vector<bool> proxy
    print("bool:", bs[0] in fl)  # tpyc: ok
    rb = Rebinding()
    # a global the iteration rebinds: the value read before `__iter__`
    print("global:", g_needle in rb)  # tpyc: ok
    print("shadow:")
    __in_lhs = 1
    ns = Nums(2)
    # a user local named like the needle's binding: the binding reads the user's
    r_shadow = __in_lhs in ns  # tpyc: ok
    print("shadow:", r_shadow)


main()
