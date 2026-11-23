# The same inline fresh argument at every other statement position.
import asyncio
from typing import Callable, Iterator
from tpy import int32, error_return, ReturnException
from fixtures import mk, total, grow, P, bump


def gen() -> Iterator[int32]:
    # generator body
    yield total(mk())  # tpyc: ok
    yield grow([1])  # tpyc: ok


async def coro() -> int32:
    # async body
    await asyncio.sleep(0)
    return total(mk()) + grow(mk())  # tpyc: ok


class Ctx:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Neg(Exception, ReturnException):
    pass


@error_return(Neg)
def checked(k: int32) -> int32:
    if k < 0:
        raise Neg
    # @error_return body
    return total(mk()) + k  # tpyc: ok


def apply(f: Callable[[int32], int32], n: int32) -> int32:
    return f(n)


def positions(k: int32) -> None:
    print("gen", list(gen()))
    print("async", asyncio.run(coro()))
    # comprehension element and condition
    print("comp", [total(mk()) + i for i in range(2) if grow([i]) > 1])  # tpyc: ok
    # lambda body
    print("lambda", apply(lambda n: total(mk()) + bump(P(n)), 1))  # tpyc: ok
    with Ctx() as c:
        # with body
        print("with", total(mk()) + c, grow(mk()))  # tpyc: ok
    try:
        # try body
        print("try", total([k, k]))  # tpyc: ok
    finally:
        # finally body
        print("finally", grow(mk()))  # tpyc: ok
    try:
        r = checked(k)
    except Neg:
        r = -1
    print("error_return", r)
    match k:
        # match guard and arm
        case 2 if total(mk()) > 5:  # tpyc: ok
            print("match", grow([k]))  # tpyc: ok
        case _:
            print("match none")


def main() -> None:
    positions(2)
