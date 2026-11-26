# Body positions: generator, nested def, with / try / match, coroutine,
# @error_return body and an @auto_readonly method pair.
import asyncio
from typing import Callable, Iterator
from tpy import int32, Own, ReturnException, auto_readonly, error_return
from fixtures import P


# gen: a generator's parameter, a frame member the lambda copies
def gen(xs: list[int32]) -> Iterator[int32]:
    f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
    yield f()
    xs.append(9)
    yield f()


# nested: a nested def's own lambda over the def's local
def nested() -> int32:
    def inner() -> Callable[[], int32]:
        ys = [1]
        r: Callable[[], int32] = lambda: len(ys)  # tpyc: warning(/copies the object 'ys'/)
        ys.append(2)
        return r
    return inner()()


# compound: inside with / try / match bodies
class Ctx:
    def __enter__(self) -> int32:
        return 1

    def __exit__(self, et, ev, tb) -> None:
        pass


def compound(code: int32) -> None:
    xs = [1]
    fs: list[Callable[[], int32]] = []
    with Ctx() as _c:
        fs.append(lambda: len(xs))  # tpyc: warning(/copies the object 'xs'/)
    try:
        fs.append(lambda: len(xs) * 10)  # tpyc: warning(/copies the object 'xs'/)
    finally:
        pass
    match code:
        case 1:
            fs.append(lambda: len(xs) * 100)  # tpyc: warning(/copies the object 'xs'/)
        case _:
            pass
    xs.append(2)
    print("compound:", fs[0](), fs[1](), fs[2]())


# async: a coroutine's parameter, a frame member the lambda copies
async def co_body(xs: list[int32]) -> int32:
    f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
    xs.append(2)
    await asyncio.sleep(0)
    return f()


# er: a local in an @error_return body is copied; a binding of an
# @error_return call's result may refer to the callee's storage, so it hedges
class Fail(Exception, ReturnException):
    pass


@error_return(Fail)
def er_make(n: int32) -> Own[P]:
    if n < 0:
        raise Fail
    return P(n)


@error_return(Fail)
def er_body(n: int32) -> int32:
    xs = [n]
    f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
    q = er_make(n)
    g: Callable[[], int32] = lambda: q.n  # tpyc: warning(/may keep its own copy of 'q'/)
    xs.append(2)
    q.n = 3
    return f() * 10 + g()


# auto_readonly: both bodies of the pair build the lambda; it warns once
class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1]

    @auto_readonly
    def reader(self) -> Callable[[], int32]:
        xs = [len(self.items)]
        f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
        xs.append(2)
        return f


def main() -> None:
    gx = [1]
    for v in gen(gx):
        print("gen:", v)
    print("nested:", nested())
    compound(1)
    print("async:", asyncio.run(co_body([1])))
    try:
        print("er:", er_body(1))
    except Fail:
        print("er: failed")
    bag = Bag()
    print("auto_readonly:", bag.reader()())
