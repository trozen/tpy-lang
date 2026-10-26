# `raise X(args)` raises the construction `X(args)`, built as the expression
# builds it, at every position; the statement adds only its tier.
from typing import Iterator, Sized
from tpy import Own, ReturnException, error_return, int32, readonly
import errs
from errs import Carrier


class KwErr(Exception):
    def __init__(self, code: int32, tag: str = "t") -> None:
        super().__init__("kw")
        self.code = code
        self.tag = tag


class Outer:
    class Err(Exception):
        def __init__(self, code: int32) -> None:
            super().__init__("outer")
            self.code = code

    class Dflt(Exception):
        def __init__(self, code: int32 = 8) -> None:
            super().__init__("dflt")
            self.code = code


class Boxed[T: Sized](Carrier):
    def __init__(self, v: Own[T]) -> None:
        super().__init__("boxed")
        self.v = v

    @readonly
    def show(self) -> str:
        return str(len(self.v))


class Lookup(Exception, ReturnException):
    def __init__(self, code: int32) -> None:
        super().__init__()
        self.code = code


class Holder:
    def __init__(self, n: int32) -> None:
        self.n = n

    def fail(self) -> None:
        # method: keyword construction reading a field
        raise KwErr(code=self.n, tag="method")  # tpyc: ok


def free_kw() -> None:
    # free function: keyword construction, throw tier
    raise KwErr(code=1)  # tpyc: ok


def gen_kw(n: int32) -> Iterator[int32]:
    yield n
    # generator: keyword construction after a suspension
    raise KwErr(code=n + 1, tag="gen")  # tpyc: ok


def dotted() -> None:
    # dotted nested class, keyword
    raise Outer.Err(code=4)  # tpyc: ok


def module_qualified() -> None:
    # module-qualified class, keyword
    raise errs.ModErr(code=5)  # tpyc: ok


def dotted_bare() -> None:
    # dotted class named without a call: `raise Outer.Dflt` raises `Outer.Dflt()`
    raise Outer.Dflt  # tpyc: ok


def module_type_args() -> int:
    # module-qualified generic with written type arguments, as the expression:
    # `int` where the argument alone would infer int32, so a dropped `[int]`
    # overflows
    b = errs.ModBox[int](2147483647)  # tpyc: ok
    b.v += 1
    print("module-targs-expr", b.v)
    if b.v > 0:
        # an int32 literal: without the written [int] it builds ModBox[int32]
        raise errs.ModBox[int](7)  # tpyc: ok
    return b.v


def generic_inferred() -> None:
    # the arguments infer the type argument: Boxed[list[int32]]
    raise Boxed([1, 2])  # tpyc: ok


@error_return(Lookup)
def find(k: int32) -> int32:
    if k > 0:
        # @error_return body: keyword construction of a return exception
        raise Lookup(code=k * 3)  # tpyc: ok
    return k


def virtual() -> None:
    # @virtual_raise: the errno picks the subclass at raise time
    raise OSError(2, "missing")  # tpyc: ok


def main() -> None:
    try:
        free_kw()
    except KwErr as e:
        print("free", e.code, e.tag)
    try:
        Holder(2).fail()
    except KwErr as e:
        print("method", e.code, e.tag)
    try:
        for v in gen_kw(2):
            print("gen-yield", v)
    except KwErr as e:
        print("gen", e.code, e.tag)
    try:
        dotted()
    except Outer.Err as e:
        print("dotted", e.code)
    try:
        module_qualified()
    except errs.ModErr as e:
        print("module", e.code)
    try:
        dotted_bare()
    except Outer.Dflt as e:
        print("dotted-bare", e.code)
    try:
        module_type_args()
    except Carrier as e:
        # the payload of the ModBox[int] the raise built
        print("module-targs", e.show())
    try:
        generic_inferred()
    except Carrier as e:
        # the payload the inferred Boxed[list[int32]] holds
        print("generic", e.show())
    try:
        print("find-ok", find(0))
        find(1)
    except Lookup as e:
        print("find", e.code)
    try:
        virtual()
    except FileNotFoundError as e:
        print("virtual", e.errno)


main()
