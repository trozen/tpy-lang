# One warning per position where a wide literal reaches a protocol parameter.
import asyncio
from typing import Iterator, overload
from tpy import Stringable, int32, dispatch, auto_readonly, error_return, ReturnException


class Fail(Exception, ReturnException):
    pass


class Ctx:
    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


@error_return(Fail)
def fallible(bad: bool) -> str:
    # @error_return body
    if bad:
        raise Fail()
    return str(5000000014)  # tpyc: warning(/passed as int \(BigInt\)/)


@overload
def ovp(x: Stringable, y: int32) -> str: ...
@overload
def ovp(x: Stringable, y: str) -> str: ...
def ovp(x: Stringable, y: int32 | str) -> str:
    return str(x)


@dispatch
def dp(x: Stringable, y: int32) -> str:
    return "dispatch " + str(x)


@dispatch
def dp(x: str) -> str:
    return "str " + x


class K:
    s: str

    def __init__(self, x: Stringable) -> None:
        self.s = str(x)

    def m(self, x: Stringable) -> str:
        return str(x)

    def __contains__(self, x: Stringable) -> bool:
        return str(x) == self.s

    @auto_readonly
    def text(self) -> str:
        # an @auto_readonly clone pair: one warning for the one source line
        return str(5000000013)  # tpyc: warning(/passed as int \(BigInt\)/)


def twice[T](x: T) -> str:
    # a generic body met at two instantiations warns once
    return str(5000000012)  # tpyc: warning(/passed as int \(BigInt\)/)


def gen() -> Iterator[str]:
    # generator
    yield str(5000000001)  # tpyc: warning(/passed as int \(BigInt\)/)


async def co() -> str:
    # async
    return str(5000000002)  # tpyc: warning(/passed as int \(BigInt\)/)


def main() -> None:
    # ctor, method, and the `in` operator's __contains__
    k = K(5000000003)  # tpyc: warning(/passed as int \(BigInt\)/)
    print("method", k.s, k.m(5000000004))  # tpyc: warning(/passed as int \(BigInt\)/)
    print("readonly", k.text())
    print("in", 5000000003 in k)  # tpyc: warning(/passed as int \(BigInt\)/)
    print("generic", twice(1), twice("a"))
    for s in gen():
        print("gen", s)
    print("async", asyncio.run(co()))
    # comprehension
    print("comp", [str(5000000005) for _ in range(1)])  # tpyc: warning(/passed as int \(BigInt\)/)
    # lambda body
    print("lambda", sorted([2, 1], key=lambda v: str(5000000006) + str(v)))  # tpyc: warning(/passed as int \(BigInt\)/)
    # typing.overload and tpy.dispatch winners
    print("overload", ovp(5000000007, 1))  # tpyc: warning(/passed as int \(BigInt\)/)
    print(dp(5000000008, 1))  # tpyc: warning(/passed as int \(BigInt\)/)
    x = 1
    match x:
        case 1:
            # match arm
            print("match", str(5000000009))  # tpyc: warning(/passed as int \(BigInt\)/)
        case _:
            pass
    try:
        # try / finally
        print("try", str(5000000010))  # tpyc: warning(/passed as int \(BigInt\)/)
    finally:
        print("finally", str(5000000011))  # tpyc: warning(/passed as int \(BigInt\)/)
    with Ctx():
        # with body
        print("with", str(5000000015))  # tpyc: warning(/passed as int \(BigInt\)/)
    try:
        print("error_return", fallible(False))
    except Fail:
        print("error_return failed")
