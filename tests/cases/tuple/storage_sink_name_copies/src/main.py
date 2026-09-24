# A tuple LOCAL stored as a container element at its last use: the elements
# the local holds by reference (bound from a borrowed name or a borrowing
# call) are deep-copied into the container and warn, as the scalar does; an
# element the local owns aliases nothing and takes no warning.
import asyncio
from typing import Iterator

from tpy import int32, Own, ReturnException, error_return


class P:
    xs: list[int32]

    def __init__(self, x: int32) -> None:
        self.xs = [x]


class Bad(Exception, ReturnException):
    pass


def mk2(p: P) -> tuple[P, P]:
    return (p, p)


def show(tag: str, ys: list[tuple[P, P]]) -> None:
    for a, b in ys:
        print(tag, len(a.xs), len(b.xs))


def lit_local(p: P) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    # free function: a literal-bound local copies both borrowed elements
    ys = [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)
    return ys


def call_local(p: P) -> Own[list[tuple[P, P]]]:
    t = mk2(p)
    # a local bound from a borrowing call copies both elements
    ys = [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)
    return ys


def mixed_local(p: P) -> Own[list[tuple[P, P]]]:
    t = (P(1), p)
    # only the borrowed element 1 warns; nothing aliases the fresh element 0
    ys = [t]  # tpyc: warning(/copies P into owned storage \(tuple element 1\)/)
    return ys


def owned_from_local(p: P) -> Own[list[tuple[P, P]]]:
    q = P(1)
    t = (q, p)
    # element 0 comes from an owned local at its last use: only the borrowed
    # element 1 warns
    ys = [t]  # tpyc: warning(/copies P into owned storage \(tuple element 1\)/)
    return ys


def scalar_mix(p: P) -> Own[list[tuple[P, int32]]]:
    t = (p, 3)
    # the value element never warns
    ys = [t]  # tpyc: warning(/copies P into owned storage \(tuple element 0\)/)
    return ys


def dict_value(p: P) -> Own[dict[int32, tuple[P, P]]]:
    t = (p, p)
    # a dict value slot copies the same way
    d = {1: t}  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)
    return d


def comp_elem(p: P) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    # a comprehension element reads the local once per iteration
    return [t for _ in range(2)]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)


def return_list(p: P) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    # the literal is the returned value
    return [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)


def nested_list(p: P) -> Own[list[list[tuple[P, P]]]]:
    t = (p, p)
    # a nested literal's element
    return [[t]]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)


def nested_tuple(p: P) -> Own[list[tuple[tuple[P, P], int32]]]:
    t = (p, p)
    # a tuple member inside the element (its read-back is
    # BUGS.md#nested-storage-tuple-element-read)
    return [(t, 1)]  # tpyc: warning(/copies P into .*tuple element 0\.0/) warning(/copies P into .*tuple element 0\.1/)


class Keeper:
    ys: list[tuple[P, P]]

    def __init__(self, p: P) -> None:
        t = (p, p)
        # constructor
        self.ys = [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)

    def again(self, p: P) -> Own[list[tuple[P, P]]]:
        t = mk2(p)
        # method
        return [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)


def gen(p: P) -> Iterator[int32]:
    t = (p, p)
    # generator
    ys = [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)
    p.xs.append(7)
    for b, c in ys:
        yield len(b.xs) * 10 + len(c.xs)


async def coro(p: P) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    await asyncio.sleep(0)
    # async
    return [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)


def closure(p: P) -> Own[list[tuple[P, P]]]:
    def inner() -> Own[list[tuple[P, P]]]:
        t = (p, p)
        # closure body
        return [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)
    return inner()


def in_finally(p: P) -> Own[list[tuple[P, P]]]:
    ys: list[tuple[P, P]] = []
    try:
        t = (p, p)
        # try body with a finally
        ys = [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)
    finally:
        print("finally")
    return ys


class Ctx:
    def __enter__(self) -> int32:
        return 0

    def __exit__(self, et, ev, tb) -> None:
        pass


def in_with(p: P) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    with Ctx():
        # context-manager body
        return [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)


def in_match(p: P, k: int32) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    match k:
        case 1:
            # match arm
            return [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)
        case _:
            return []


@error_return(Bad)
def in_error_return(p: P) -> Own[list[tuple[P, P]]]:
    t = (p, p)
    # @error_return body, through a local: the bare `return [t]` is
    # BUGS.md#error-return-list-literal-return
    ys = [t]  # tpyc: warning(/copies P into .*tuple element 0/) warning(/copies P into .*tuple element 1/)
    return ys


def main() -> None:
    a = P(1)
    y1 = lit_local(a)
    y2 = call_local(a)
    y3 = mixed_local(a)
    y4 = scalar_mix(a)
    y5 = dict_value(a)
    y6 = return_list(a)
    y15 = comp_elem(a)
    y7 = nested_list(a)
    y8 = nested_tuple(a)
    k = Keeper(a)
    y9 = k.again(a)
    y11 = asyncio.run(coro(a))
    y12 = closure(a)
    y13 = in_finally(a)
    y14 = in_with(a)
    y16 = owned_from_local(a)
    y17 = in_match(a, 1)
    y18: list[tuple[P, P]] = []
    try:
        y18 = in_error_return(a)
    except Bad:
        print("bad")
    # mutate after every boundary: a copied element does not see it
    a.xs.append(7)
    show("lit_local", y1)
    show("call_local", y2)
    show("mixed_local", y3)
    for b4, n4 in y4:
        print("scalar_mix", len(b4.xs), n4)
    b5, c5 = y5[1]
    print("dict_value", len(b5.xs), len(c5.xs))
    show("return_list", y6)
    show("comp_elem", y15)
    for inner in y7:
        show("nested_list", inner)
    # a nested tuple element of a storage list does not read back yet; the
    # warning and the emitted lift are the pin
    print("nested_tuple", len(y8))
    show("ctor", k.ys)
    show("method", y9)
    for n in gen(P(1)):
        print("gen", n)
    show("async", y11)
    show("closure", y12)
    show("finally", y13)
    show("with", y14)
    show("owned_from_local", y16)
    show("match", y17)
    show("error_return", y18)


main()
