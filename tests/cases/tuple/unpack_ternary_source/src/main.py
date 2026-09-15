# `a, b = t1 if c else t2` -- a SELECT over two tuple sources at an unpack.
# Both holder spellings are pinned in the .cpp, and for the scalar-element
# sections only the .cpp can catch a regression (those elements copy out either
# way): two same-typed lvalue arms whose elements all copy make the conditional
# a C++ lvalue and the holder const-ref-binds it, while a prvalue arm keeps the
# by-value rvalue capture a call / subscript / field source takes. A str or
# bytes target is a VIEW into the holder, so its tuple keeps the owning holder
# too -- a const-ref holder would make the target alias the selected SOURCE and
# a reseat of that source before the read would show the new value where
# CPython keeps the old (`reseat_view`, and `nested_view` for a view element
# under a NESTED tuple). That is a property of the TARGET, not of the element:
# a frame field and a module-level global are owning slots the element COPIES
# into, so those positions keep the const-ref holder even with a str element
# (`gen_view` and the module-level section) -- a by-value holder there would
# deep-copy the whole tuple per evaluation for nothing.
import asyncio
from typing import Iterator
from tpy import int32


class Picker:
    lo: int32

    def __init__(self, lo: int32) -> None:
        self.lo = lo

    # method: the select is over two params, off a record receiver
    def sum(self, c: bool, t1: tuple[int32, int32],
            t2: tuple[int32, int32]) -> int32:
        x, y = t1 if c else t2  # tpyc: ok
        return self.lo + x + y


def values(c: bool, t1: tuple[int32, int32], t2: tuple[int32, int32]) -> int32:
    # free function: the select over two params
    a, b = t1 if c else t2  # tpyc: ok
    return a + b


def make(n: int32) -> tuple[int32, int32]:
    return (n, n + 1)


def prvalue_arm(c: bool, t1: tuple[int32, int32]) -> int32:
    # a CALL arm makes the select a prvalue: the holder stays by value
    a, b = t1 if c else make(7)  # tpyc: ok
    return a + b


def local_arms(c: bool) -> int32:
    t1 = (1, 2)
    t2 = (30, 40)
    # both arms are LOCALS rather than params: still an lvalue select
    a, b = t1 if c else t2  # tpyc: ok
    return a + b


def strings(c: bool, t1: tuple[str, int32], t2: tuple[str, int32]) -> str:
    # a str element binds a view into the holder
    s, n = t1 if c else t2  # tpyc: ok
    return s + str(n)


def gen(c: bool, t1: tuple[int32, int32],
        t2: tuple[int32, int32]) -> Iterator[int32]:
    # generator: the unpack lands on the resumable frame
    a, b = t1 if c else t2  # tpyc: ok
    yield a
    yield b


async def coro(c: bool, t1: tuple[int32, int32],
               t2: tuple[int32, int32]) -> int32:
    a, b = t1 if c else t2  # tpyc: ok
    await asyncio.sleep(0)
    return a + b


def reseat_view(c: bool) -> None:
    t1 = ("aa", 1)
    t2 = ("zz", 9)
    # a view target: the source is reseated between the unpack and the read
    s, n = t1 if c else t2  # tpyc: ok
    t1 = ("qq", 5)
    print("reseat_view", s, n, t1[0])


def reseat_value(c: bool) -> None:
    t1 = (1, 2)
    t2 = (30, 40)
    # the value-only twin, whose const-ref holder the reseat cannot reach
    a, b = t1 if c else t2  # tpyc: ok
    t1 = (7, 8)
    print("reseat_value", a, b, t1[0])


def nested_view(c: bool) -> None:
    t1 = (("aa", 1), 2)
    t2 = (("zz", 9), 3)
    # a view element under a NESTED tuple: the holder is still by value
    p, n = t1 if c else t2  # tpyc: ok
    t1 = (("qq", 5), 4)
    print("nested_view", p[0], p[1], n)


def gen_view(c: bool) -> Iterator[str]:
    t1 = ("aa", 1)
    t2 = ("zz", 9)
    # generator: the targets are frame fields, owning slots, so the const-ref
    # holder is right even with a str element -- the reseat across the yield
    # stays invisible
    s, n = t1 if c else t2  # tpyc: ok
    yield s
    t1 = ("qq", 5)
    yield s + str(n)


# module level: the targets are globals, owning slots, same as the frame
mc = True
mt1 = ("aa", 1)
mt2 = ("zz", 9)
ms, mn = mt1 if mc else mt2  # tpyc: ok
mt1 = ("qq", 5)
print("module", ms, mn)


def main() -> None:
    print("values", values(True, (1, 2), (3, 4)))
    print("prvalue_arm", prvalue_arm(False, (1, 2)))
    print("local_arms", local_arms(False))
    print("strings", strings(False, ("a", 1), ("b", 2)))
    reseat_view(True)
    reseat_value(True)
    nested_view(True)
    for sv in gen_view(True):
        print("gen_view", sv)
    print("method", Picker(10).sum(False, (1, 2), (3, 4)))
    for v in gen(True, (1, 2), (3, 4)):
        print("gen", v)
    print("async", asyncio.run(coro(False, (1, 2), (3, 4))))


main()
