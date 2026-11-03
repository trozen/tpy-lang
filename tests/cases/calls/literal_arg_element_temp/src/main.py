# A container literal argument whose element needs a statement temporary (a
# reference record built for an Optional parameter, `opt(V3(2.0))`) hoists
# that temporary as the same element does outside the literal.
from typing import Iterator
from tpy import copy, Own, ValueType, int32
from tplib import Box


class V3:
    x: float

    def __init__(self, x: float) -> None:
        self.x = x

    def opt_m(self, e: "V3 | None" = None) -> int:
        return 0 if e is None else int(e.x)


class Material:
    emission: V3

    def __init__(self, color: V3, emission: V3 | None = None) -> None:
        self.emission = V3(0.0) if emission is None else copy(emission)


class Body:
    material: Box[Material]

    def __init__(self, material: Own[Box[Material]]) -> None:
        self.material = material


class Scene:
    objects: list[Body]

    def __init__(self, objects: Own[list[Body]]) -> None:
        self.objects = objects


class C(ValueType):
    r: int32

    def __init__(self, r: int32) -> None:
        self.r = r


def vopt(c: C | None) -> int:
    return 0 if c is None else c.r


def first() -> int:
    print("first")
    return 10


def opt(e: V3 | None = None) -> int:
    return 0 if e is None else int(e.x)


class Keep:
    xs: list[int]

    def __init__(self, xs: Own[list[int]]) -> None:
        self.xs = xs


def own_ints(xs: Own[list[int]]) -> int:
    k = Keep(xs)
    return sum(k.xs)


def own_set(xs: Own[set[int]]) -> Own[set[int]]:
    return xs


def uni(e: V3 | int32) -> int:
    return int(e.x) if isinstance(e, V3) else int(e)


def take_i(n: int) -> int:
    return n


def take_open_own[T](xs: Own[T]) -> Own[T]:
    return xs


def own_nested(xs: Own[list[list[int]]]) -> Own[list[list[int]]]:
    return xs


def gen() -> Iterator[int]:
    # generator body
    yield own_ints([opt(V3(5.0))])  # tpyc: ok


def main() -> None:
    # the constructor chain: Scene([Body(Box(Material(..., V3(2.0))))])
    s = Scene([Body(Box(Material(V3(1.0), V3(2.0))))])  # tpyc: ok
    print("scene:", s.objects[0].material.get().emission.x)
    # free-function element at an owned list parameter
    print("own:", own_ints([opt(V3(2.0)), opt()]))  # tpyc: ok
    # set literal
    print("set:", len(own_set({opt(V3(3.0))})))  # tpyc: ok
    # an operator over the element at a scalar parameter
    print("binop:", take_i(opt(V3(2.0)) + 1))  # tpyc: ok
    # an open Own[T] parameter
    print("open:", len(take_open_own([opt(V3(2.0))])))  # tpyc: ok
    # a method-call element
    v = V3(1.0)
    print("method:", own_ints([v.opt_m(V3(8.0))]))  # tpyc: ok
    # a nested literal element
    nest = own_nested([[opt(V3(6.0))]])  # tpyc: ok
    print("nested:", nest[0][0])
    # a union parameter's element
    print("union param:", own_ints([uni(V3(2.0))]))  # tpyc: ok
    print("gen:", list(gen()))
    n = 0
    # while condition, re-tested each pass
    while own_ints([opt(V3(1.0)), n]) < 3:  # tpyc: ok
        n += 1
    print("while:", n)
    # right of `and`
    print("and:", n > 0 and own_ints([opt(V3(2.0))]) == 2)  # tpyc: ok
    # the hoisted V3(2.0) runs ahead of first(), as before
    # (BUGS.md#subexpression-right-to-left-eval); only first() is observable
    r_order = own_ints([first(), opt(V3(2.0))])  # tpyc: ok
    print("order:", r_order)
    # inverse: a ValueType record needs no temporary
    print("value:", own_ints([vopt(C(2))]))  # tpyc: ok
    # inverse: a local literal keeps its render
    xs = [opt(V3(7.0))]
    print("local:", own_ints(xs))


main()
