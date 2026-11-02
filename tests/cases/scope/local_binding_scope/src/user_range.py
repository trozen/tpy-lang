# A module function named `range` shadows the builtin for this module: a
# `for`, a comprehension, a generator's and a coroutine's `for` over it
# iterate the list it returns, never a counted loop. Its class and
# exception class are the ones the case imports into type positions, and it
# re-exports the builtin `len` as `size`.
import asyncio
from builtins import len as size
from enum import Enum
from typing import Iterator
from tpy import int32, Own


def range(n: int32) -> Own[list[int32]]:
    return [n, n]


def for_over() -> None:
    # The `for` over the module's `range`.
    for i in range(3):
        print("user_range for", i)


def comp_over() -> None:
    # The comprehension over the module's `range`.
    print("user_range comp", [i for i in range(2)])


def gen_over() -> Iterator[int32]:
    # A generator's `for` over the module's `range`.
    for i in range(4):
        yield i


async def coro_over() -> int32:
    # A coroutine's `for` over the module's `range`.
    t = 0
    for i in range(5):
        await asyncio.sleep(0)
        t += i
    return t


class Shape:
    def __init__(self, n: int32) -> None:
        self.n = n


class Oops(Exception):
    pass


class Hue(Enum):
    RED = 1
    BLUE = 2
