# A local first bound in the non-suspending `finally` of a suspending `try`
# (the frame's finally member function) is registered as a frame local and
# reads, writes and aliases like any other local, in every frame shape.
import asyncio
from typing import Iterator
from tpy import int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def pick(items: list[Box], i: int32) -> Box | None:
    if i < len(items):
        return items[i]
    return None


# gen_fn: free generator function, fresh bind then augmented assign
def gen_fn() -> Iterator[int32]:
    try:
        yield 1
    finally:
        t = 0  # tpyc: ok
        t += 1
        print("gen_fn fin", t)


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    # gen_method: generator method reading and writing self
    def gen_method(self) -> Iterator[int32]:
        try:
            yield self.n
        finally:
            t = self.n + 1  # tpyc: ok
            self.n = t
            print("gen_method fin", t)

    # async_method: async method reading and writing self
    async def async_method(self) -> int32:
        try:
            await asyncio.sleep(0)
        finally:
            t = self.n + 10  # tpyc: ok
            self.n = t
            print("async_method fin", t)
        return self.n


# async_fn: free async function
async def async_fn() -> int32:
    try:
        await asyncio.sleep(0)
    finally:
        t = 5  # tpyc: ok
        t += 1
        print("async_fn fin", t)
    return 7


# for_in_finally: a fresh local inside a loop in the finally
def for_in_finally() -> Iterator[int32]:
    try:
        yield 1
    finally:
        for i in range(2):
            t = i * 2  # tpyc: ok
            print("for_in_finally fin", t)


# if_in_finally: a fresh local inside a branch of the finally
def if_in_finally(flag: bool) -> Iterator[int32]:
    try:
        yield 1
    finally:
        if flag:
            t = 4  # tpyc: ok
            print("if_in_finally fin", t)


# while_in_finally: a fresh local inside a while loop of an async finally
async def while_in_finally() -> None:
    try:
        await asyncio.sleep(0)
    finally:
        i = 0  # tpyc: ok
        while i < 2:
            j = i * 3  # tpyc: ok
            print("while_in_finally fin", j)
            i += 1


# nested_try_finally: the inner and the outer finally both bind
def nested_try_finally() -> Iterator[int32]:
    try:
        try:
            yield 1
        finally:
            t = 1  # tpyc: ok
            print("nested_try_finally inner", t)
    finally:
        u = 2  # tpyc: ok
        print("nested_try_finally outer", u)


# read_after_try: a name first bound in the finally, read after the try
def read_after_try() -> Iterator[int32]:
    try:
        yield 1
    finally:
        t = 9  # tpyc: ok
    yield t
    print("read_after_try after", t)


# unpack_literal: a literal tuple unpack in the finally
def unpack_literal() -> Iterator[int32]:
    try:
        yield 1
    finally:
        a, b = 1, 2  # tpyc: ok
        print("unpack_literal fin", a, b)


# annotated: an annotated first bind
def annotated() -> Iterator[int32]:
    try:
        yield 1
    finally:
        t: int32 = 0  # tpyc: ok
        t += 2
        print("annotated fin", t)


# return_path: the finally runs on an early return
def return_path(stop: bool) -> Iterator[int32]:
    try:
        yield 1
        if stop:
            return
        yield 2
    finally:
        t = 3  # tpyc: ok
        print("return_path fin", t)


# break_path: the finally runs on a break out of the generator's own loop
def break_path() -> Iterator[int32]:
    for i in range(3):
        try:
            yield i
            if i == 1:
                break
        finally:
            t = i * 10  # tpyc: ok
            print("break_path fin", t)
    print("break_path done")


# closed_early: the consumer abandons the generator mid-try
def closed_early() -> Iterator[int32]:
    try:
        yield 1
        yield 2
    finally:
        t = 7  # tpyc: ok
        print("closed_early fin", t)


# exception_path: an exception propagates through the finally
def exception_path() -> Iterator[int32]:
    try:
        yield 1
        raise ValueError("boom")
    finally:
        t = 3  # tpyc: ok
        print("exception_path fin", t)


# str_local: a str local
def str_local() -> Iterator[str]:
    try:
        yield "a"
    finally:
        s = "fin"  # tpyc: ok
        print("str_local " + s)


# list_local: a list local mutated after the bind
def list_local() -> Iterator[int32]:
    try:
        yield 1
    finally:
        xs = [1, 2]  # tpyc: ok
        xs.append(3)
        print("list_local fin", xs)


# alias: a record alias bound in the finally; the write must reach the caller's list
def alias(items: list[Box]) -> Iterator[int32]:
    try:
        yield 1
    finally:
        b = items[0]  # tpyc: ok
        b.n += 1
        print("alias fin", b.n)


# optional_ref: an Optional reference-type local narrowed in the finally;
# the write through it must reach the caller's list
def optional_ref(items: list[Box]) -> Iterator[int32]:
    try:
        yield 1
    finally:
        o = pick(items, 0)  # tpyc: ok
        if o is not None:
            o.n += 1
            print("optional_ref fin", o.n)


class Guard:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> int32:
        print("with_in_finally enter")
        return self.n

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("with_in_finally exit")


# with_in_finally: a with-target first bound in the finally
def with_in_finally() -> Iterator[int32]:
    try:
        yield 1
    finally:
        with Guard(8) as x:  # tpyc: ok
            print("with_in_finally fin", x)


# tuple_local: a tuple local
def tuple_local() -> Iterator[int32]:
    try:
        yield 1
    finally:
        p = (1, "a")  # tpyc: ok
        print("tuple_local fin", p[0], p[1])


def main() -> None:
    for v in gen_fn():
        print("gen_fn y", v)
    c = Counter()
    for v in c.gen_method():
        print("gen_method y", v)
    print("gen_method n", c.n)
    r = asyncio.run(async_fn())
    print("async_fn ret", r)
    m = asyncio.run(c.async_method())
    print("async_method ret", m)
    for v in for_in_finally():
        print("for_in_finally y", v)
    for v in if_in_finally(True):
        print("if_in_finally y", v)
    for v in if_in_finally(False):
        print("if_in_finally y", v)
    asyncio.run(while_in_finally())
    for v in nested_try_finally():
        print("nested_try_finally y", v)
    for v in read_after_try():
        print("read_after_try y", v)
    for v in unpack_literal():
        print("unpack_literal y", v)
    for v in annotated():
        print("annotated y", v)
    for v in return_path(True):
        print("return_path y", v)
    for v in return_path(False):
        print("return_path y", v)
    for v in break_path():
        print("break_path y", v)
    for v in closed_early():
        print("closed_early y", v)
        break
    print("closed_early after")
    try:
        for v in exception_path():
            print("exception_path y", v)
    except ValueError as e:
        print("exception_path caught", e)
    for s in str_local():
        print("str_local y " + s)
    for v in list_local():
        print("list_local y", v)
    items = [Box(1)]
    for v in alias(items):
        print("alias y", v)
    print("alias after", items[0].n)
    boxes = [Box(5)]
    for v in optional_ref(boxes):
        print("optional_ref y", v)
    print("optional_ref after", boxes[0].n)
    for v in with_in_finally():
        print("with_in_finally y", v)
    for v in tuple_local():
        print("tuple_local y", v)


main()
