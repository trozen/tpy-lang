# An exception built without a message argument carries its TPy constructor's
# default, '' as under CPython; `raise X(...)` constructs as `X(...)` does.
import asyncio
from asyncio import CancelledError
from typing import Iterator
from tpy import Own, ReturnException, error_return


class RetErr(Exception, ReturnException):
    code: int

    def __init__(self, code: int) -> None:
        # A return exception's parent init renders no call: nothing to fill.
        super().__init__()  # tpyc: ok
        self.code = code


class RetErrExplicit(Exception, ReturnException):
    code: int

    def __init__(self, code: int) -> None:
        Exception.__init__(self)  # tpyc: ok
        self.code = code


@error_return(RetErr)
def parse(s: str) -> int:
    if s == "":
        raise RetErr(3)
    return len(s)


@error_return(RetErrExplicit)
def parse_explicit(s: str) -> int:
    if s == "":
        raise RetErrExplicit(4)
    return len(s)


class Thrown(ValueError):
    def __init__(self) -> None:
        # A thrown subclass's parent init passes the default message.
        super().__init__()  # tpyc: ok


class Plain(ValueError):
    pass


class GE[U](Exception):
    a: U

    def __init__(self, a: Own[U]) -> None:
        super().__init__("ge")
        self.a = a

    def put(self, a: Own[U]) -> None:
        self.a = a


class HE[W](GE[list[W]]):
    pass


class ME[T](HE[tuple[T, T]]):
    pass


class LE(ME[int]):
    def via_super(self) -> int:
        super().put([(5, 6), (7, 8)])
        return len(self.a)

    def via_named(self) -> int:
        HE.put(self, [(9, 9)])
        return len(self.a)


class EB[T](Exception):
    x: T

    def __init__(self, x: Own[T]) -> None:
        super().__init__("eb")
        self.x = x


class CM:
    def __enter__(self) -> "CM":
        return self

    def __exit__(self, et: None, ev: BaseException | None, tb: None) -> bool:
        if ev is not None:
            print("generatorexit-abandoned", repr(str(ev)))
        return False


def gen() -> Iterator[int]:
    with CM():
        yield 1
        yield 2


def gen_section() -> None:
    g = gen()
    for v in g:
        print("generator-value", v)
        break


async def sleeper() -> None:
    await asyncio.sleep(10.0)


async def cancel_section() -> None:
    t = asyncio.create_task(sleeper())
    await asyncio.sleep(0.0)
    t.cancel()
    try:
        await t
    except CancelledError as e:
        print("cancel-task", repr(str(e)))
    try:
        raise CancelledError()  # tpyc: ok
    except CancelledError as e2:
        print("cancel-raise", repr(str(e2)))
    print("cancel-direct", repr(str(CancelledError())))  # tpyc: ok


def main() -> None:
    try:
        parse("")
    except RetErr as e:
        print("return-only", e.code, repr(str(e)))
    try:
        parse_explicit("")
    except RetErrExplicit as e2:
        print("return-only-explicit", e2.code, repr(str(e2)))
    try:
        raise Thrown()
    except Thrown as t:
        print("thrown-subclass", repr(str(t)))
    try:
        raise ValueError()  # tpyc: ok
    except ValueError as v:
        print("raise-builtin", repr(str(v)))
    try:
        # An inherited constructor takes the argument as the base's does.
        raise Plain("hello")  # tpyc: ok
    except Plain as p:
        print("inherited-arg", repr(str(p)))
    try:
        raise Plain()  # tpyc: ok
    except Plain as p2:
        print("inherited-argless", repr(str(p2)))
    # A constructor inherited over two generic levels takes the argument at
    # the class's own type parameter.
    m = ME[int]([(1, 2)])  # tpyc: ok
    m.a.append((3, 4))
    print("inherited-two-level", len(m.a), m.a[0] == (1, 2))
    # A method declared two generic levels up, called on an instance, through
    # super() and through a named intermediate base.
    m.put([(3, 4)])  # tpyc: ok
    print("method-two-level", len(m.a))
    le = LE([(1, 2)])
    print("method-two-level-parent", le.via_super(), le.via_named())
    try:
        # The raise infers the generic exception's type argument.
        raise EB([1, 2])  # tpyc: ok
    except Exception as eb:
        print("raise-generic-inferred", repr(str(eb)))
    gen_section()
    print("generatorexit-direct", repr(str(GeneratorExit())))  # tpyc: ok
    asyncio.run(cancel_section())


main()
