# A None-narrowed `Optional[list[int32]]` NAME passed at its pointee
# `list[int32]` slot -- the wide-ptr-opt deref. One section per CALLEE family,
# since the row that admits it used to sit only in the free-function table.
# Every callee appends through the parameter and the caller prints the source
# list afterwards, so a silent copy loses the appended element.
# The protocol-method family is covered by protocols/proto_param_forms.
from typing import Optional
from tpy import Own, int32


class Sink:
    tag: int32

    def __init__(self, tag: int32) -> None:
        self.tag = tag

    def absorb(self, xs: list[int32]) -> None:
        xs.append(2)

    @staticmethod
    def collect(xs: list[int32]) -> None:
        xs.append(3)


class Filler:
    n: int32

    def __init__(self, xs: list[int32]) -> None:
        xs.append(4)
        self.n = len(xs)


class GR[T]:
    t: T

    def __init__(self, t: Own[T]) -> None:
        self.t = t

    def absorb(self, xs: list[int32]) -> None:
        xs.append(7)


def take(xs: list[int32]) -> None:
    xs.append(1)


def gen_take[T](x: T, xs: list[int32]) -> None:
    print(x)
    xs.append(6)


# free function -- the one family that already carried the row
def free_fn() -> None:
    o: Optional[list[int32]] = [0]
    if o is not None:
        take(o)  # tpyc: ok
        print("free_fn", o)


# record method
def method() -> None:
    o: Optional[list[int32]] = [0]
    s = Sink(0)
    if o is not None:
        s.absorb(o)  # tpyc: ok
        print("method", o)


# @staticmethod -- the qualified-call family
def static_method() -> None:
    o: Optional[list[int32]] = [0]
    if o is not None:
        Sink.collect(o)  # tpyc: ok
        print("static_method", o)


# constructor
def constructor() -> None:
    o: Optional[list[int32]] = [0]
    if o is not None:
        f = Filler(o)  # tpyc: ok
        print("constructor", o, f.n)


# generic free function
def generic_fn() -> None:
    o: Optional[list[int32]] = [0]
    if o is not None:
        gen_take(1, o)  # tpyc: ok
        print("generic_fn", o)


# method of a generic record
def generic_method() -> None:
    o: Optional[list[int32]] = [0]
    g = GR(1)
    if o is not None:
        g.absorb(o)  # tpyc: ok
        print("generic_method", o)


def main() -> None:
    free_fn()
    method()
    static_method()
    constructor()
    generic_fn()
    generic_method()


main()
