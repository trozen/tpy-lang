# Tuple literal shapes the lowering still rejects get their member copy
# warning from sema first, so the copy is declared the day the shape lowers:
# a nested member at a walrus, at an Own argument, at an append, at a yield
# into a nested Own element, a direct member at a dict subscript store, and a
# ternary member at a field store.
from typing import Iterator

from tpy import Own, int32


class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    q: tuple[int32, P]

    def __init__(self) -> None:
        self.q = (0, P(0))


def sink(t: Own[tuple[int32, tuple[int32, P]]]) -> None:
    pass


def walrus_nested(c: P) -> None:
    # walrus: the nested member lands in the binding's owned storage.
    print((u := (1, (2, c)))[0])  # tpyc: warning(/copies P into owned storage \(tuple element 1.1\)/) error(/expr\.walrus/)
    print(u[1][1].v)


def own_arg_nested(c: P) -> None:
    # Own argument: the nested member is storage inside the owned value.
    sink((1, (2, c)))  # tpyc: warning(/copies P into owned storage \(tuple element 1.1\)/)


def append_nested(c: P) -> None:
    # append: the same nested member through list.append's Own slot.
    xs: list[tuple[int32, tuple[int32, P]]] = []
    xs.append((1, (2, c)))  # tpyc: warning(/copies P into owned storage \(tuple element 1.1\)/)


# generator yield: a borrowed member of a NESTED Own element. The generator
# lowers; reading the nested element off the yielded tuple does not yet.
def yield_nested(c: P) -> Iterator[tuple[int32, tuple[int32, Own[P]]]]:
    yield (1, (2, c))  # tpyc: warning(/copies P into owned storage \(tuple element 1.1\)/)


def dict_setitem_direct(c: P) -> None:
    # dict subscript store: the direct member copies into the container.
    d: dict[int32, tuple[int32, P]] = {}
    d[0] = (1, c)  # tpyc: warning(/copies P into container \(tuple element 1\)/)


def field_ternary(h: H, c: P, flag: bool) -> None:
    # field store: a ternary arm names a reference, and that arm copies.
    h.q = (1, c if flag else P(2))  # tpyc: warning(/copies P into field \(tuple element 1\)/)


def main() -> None:
    c = P(1)
    walrus_nested(c)
    own_arg_nested(c)
    append_nested(c)
    dict_setitem_direct(c)
    field_ternary(H(), c, True)


main()
