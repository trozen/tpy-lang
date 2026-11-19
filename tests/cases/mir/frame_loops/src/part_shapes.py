# Decomposed loops in each control-flow shape a suspending body gives them.
from typing import Iterator

from tpy import int32


# nested: two suspending loops, one inside the other (two loop uids)
def grid(n: int32) -> Iterator[int32]:  # tpyc: mir(covered)
    for i in range(n):
        for j in range(n):
            yield i * 10 + j


# reused_name: one variable name bound by two suspending loops in turn
def twice(n: int32) -> Iterator[int32]:  # tpyc: mir(covered)
    for i in range(n):
        yield i
    for i in range(2):
        yield i + 100


# continue_else: `continue` past a yield, and a yield in the loop's `else`
def evens(n: int32) -> Iterator[int32]:  # tpyc: mir(covered)
    for i in range(n):
        if i % 2 == 1:
            continue
        yield i
    else:
        yield -1


# var_written: the loop variable is written in the body; the next pass rebinds it
def bumped(n: int32) -> Iterator[int32]:  # tpyc: mir(covered)
    for i in range(n):
        i += 100
        yield i


# inner_plain: a loop over a parameter that does not suspend, inside the frame, yields outside it
def total_then(xs: list[int32]) -> Iterator[int32]:  # tpyc: mir(covered)
    yield 0
    total = 0
    for x in xs:
        total += x
    yield total


# dead_cursor: the only yields are before the loop and in its `else`: no cursor is open at a yield
def tail(xs: list[int32], stop: int32) -> Iterator[int32]:  # tpyc: mir(covered)
    yield len(xs)
    for x in xs:
        if x == stop:
            break
    else:
        yield -1


# break_return: `break` and `return` out of a suspending loop
def until(n: int32, stop: int32) -> Iterator[int32]:  # tpyc: mir(covered)
    for i in range(n):
        if i == stop:
            break
        yield i
    for i in range(n):
        if i == stop:
            return
        yield i + 50


def main() -> None:
    print("nested:", list(grid(2)))
    print("reused_name:", list(twice(2)))
    print("continue_else:", list(evens(5)))
    print("var_written:", list(bumped(3)))
    print("inner_plain:", list(total_then([1, 2, 3])))
    print("dead_cursor:", list(tail([1, 2, 3], 2)), list(tail([1, 2], 9)))
    print("break_return:", list(until(5, 2)))
