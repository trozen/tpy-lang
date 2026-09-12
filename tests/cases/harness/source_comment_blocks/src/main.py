# Source-comment placement in generated C++: a declaration echoes its signature,
# an implementation echoes its whole Python definition above the C++ (the C++ is the assertion).
import asyncio
from typing import Iterator

from tpy import int32, readonly


# free function: a multi-line literal and an inner comment keep their shape
def shaped(a: int32, b: int32) -> int32:
    cfg = {
        "x": a,
        "y": b,
    }
    # inner comment stays with its statement
    return cfg["x"] + cfg["y"]


# free function: a triple-quoted string keeps its inner lines verbatim
def quoted() -> str:
    text = """first
    second
        third"""
    return text


# method and constructor: the class declaration echoes members, each
# implementation echoes its own body
class Counter:
    n: int32

    def __init__(self, start: int32) -> None:
        self.n = start
        self.n += 0

    def bump(self, by: int32) -> int32:
        self.n += by
        return self.n


# simple generator: the block sits above the lambda-backed definition
def upto(n: int32) -> Iterator[int32]:
    for i in range(n):
        yield i


# resumable generator: the block sits above the frame's __next__ implementation
def pair(n: int32) -> Iterator[int32]:
    yield n
    if n > 0:
        yield n + 1


# async: the block sits above the frame's poll implementation
async def doubled(n: int32) -> int32:
    await asyncio.sleep(0)
    return n * 2


# commented definition: the comment rides above the def line inside the block
def helper(f: int32) -> int32:
    return f + 1


# decorated method: the decorator rides above the def line inside the block
class Reader:
    n: int32

    def __init__(self) -> None:
        self.n = 2

    @readonly
    def peek(self) -> int32:
        return self.n + 1


def main() -> None:
    print("shaped", shaped(1, 2))
    print("quoted", quoted())
    c = Counter(5)
    print("method", c.bump(3))
    print("simple-gen", list(upto(3)))
    print("resumable-gen", list(pair(1)))
    print("async", asyncio.run(doubled(4)))
    print("helper", helper(1))
    print("decorated", Reader().peek())


# module-level multi-line statement: the module body is the block above __tpy_init
TABLE = {
    "a": 1,
    "b": 2,
}
print("module", TABLE["a"] + TABLE["b"])
main()
