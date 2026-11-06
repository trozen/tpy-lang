# An owned parameter, generic or not, dies at the return: a generic body
# returns it by value only where every instantiation does (a ValueType-bounded
# `-> T`, an `Own[...]` return); the lending shapes are refused as at the
# concrete twin (see error_own_generic_param_return and
# error_own_generic_param_borrow_return). Every result is read
# after another call, so a dangling return would show.
import asyncio
from tpy import int32, Own, ValueType, readonly


class P:
    def __init__(self, v: int32) -> None:
        self.v = v


def churn() -> int32:
    a = [P(70), P(80), P(90)]
    return a[0].v + a[1].v


# free function: a ValueType-bounded owned T returns by value
def passthrough[T: ValueType](x: Own[T]) -> T:
    return x  # tpyc: ok


# readonly: a ValueType-bounded owned T returned readonly is a value too
def ro_back[T: ValueType](x: Own[T]) -> readonly[T]:
    return x  # tpyc: ok


# method: a class-bounded T | None return holds its value
class C[T: ValueType]:
    def opt(self, x: Own[T]) -> T | None:
        return x  # tpyc: ok


# Own[T] return: the owned value moves out, at a value and a class T
def back[T](x: Own[T]) -> Own[T]:
    return x  # tpyc: ok


# async: a ValueType-bounded owned T returned from a coroutine
async def aback[T: ValueType](x: Own[T]) -> T:
    await asyncio.sleep(0)
    return x  # tpyc: ok


async def run_async() -> None:
    r = await aback(11)
    churn()
    print("async", r)


def main() -> None:
    a = passthrough(7)
    churn()
    print("passthrough", a)

    r = ro_back(8)
    churn()
    print("readonly", r)

    c = C[int32]()
    o = c.opt(6)
    churn()
    print("method", o)

    v = back(5)
    churn()
    print("own value", v)
    p = back(P(3))
    churn()
    p.v += 1
    print("own class", p.v)

    asyncio.run(run_async())


main()
