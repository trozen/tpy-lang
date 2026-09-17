# Resumable frames stack an empty join/resume case onto the case it falls into;
# each section yields from a loop inside a region whose jumps must still land.
import asyncio
from typing import Iterator
from tpy import int32


class Ctx:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> "Ctx":
        print("enter", self.name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit", self.name)


# free generator: the resume and the filter miss both reach the loop head
def filtered(xs: list[int32], k: int32) -> Iterator[int32]:
    for x in xs:
        if x % 3 != k:  # tpyc: ok
            yield x * 2


# try/finally: the forwarded cases sit inside the try region
def in_try(xs: list[int32]) -> Iterator[int32]:
    try:
        for x in xs:
            if x > 1:
                yield x
    finally:
        print("in_try finally")


# context manager: the forwarded cases sit inside the with region
def in_with(xs: list[int32]) -> Iterator[int32]:
    with Ctx("w"):
        for x in xs:
            if x != 2:
                yield x


# match arm yields inside the loop
def in_match(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        match x:
            case 1:
                yield 10
            case 2:
                pass
            case _:
                yield x


# break/continue inside a try in the loop
def break_continue(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        try:
            if x == 2:
                continue
            if x == 4:
                break
            yield x
        finally:
            print("bc finally", x)


# return inside finally stops the generator after the cleanup
def return_in_finally(xs: list[int32]) -> Iterator[int32]:
    for x in xs:
        try:
            yield x
        finally:
            if x == 2:
                return


# abandoned while suspended inside try: the frame's cleanup still runs
def abandoned(xs: list[int32]) -> Iterator[int32]:
    try:
        for x in xs:
            if x > 0:
                yield x
    finally:
        print("abandoned finally")


# a generator with a return inside a finally renders the stop check on each
# same-region jump inside a try, so those cases keep their own label (a render pin)
def stop_in_region(xs: list[int32]) -> Iterator[int32]:
    try:
        for x in xs:
            if x != 2:  # tpyc: ok
                yield x
    except ValueError:
        yield -1
    for x in xs:
        try:
            yield x * 10
        finally:
            if x == 2:
                return


# async body: an await inside a filtered loop
async def async_loop(xs: list[int32]) -> int32:
    total = 0
    for x in xs:
        if x % 2 == 0:
            await asyncio.sleep(0)
            total += x
    return total


# async body: the await and the filter miss join inside a try/finally region
async def async_try_loop(xs: list[int32]) -> int32:
    total = 0
    try:
        for x in xs:
            if x % 2 == 1:  # tpyc: ok
                await asyncio.sleep(0)
                total += x
    finally:
        print("async_try_loop finally")
    return total


def take_first(xs: list[int32]) -> int32:
    for v in abandoned(xs):
        return v
    return -1


def main() -> None:
    xs = [1, 2, 3, 4, 5]
    # Results are bound before printing: a print argument that itself prints
    # interleaves (BUGS.md#print-arg-output-interleaves).
    filtered_r = list(filtered(xs, 1))
    print("filtered", filtered_r)
    in_try_r = list(in_try(xs))
    print("in_try", in_try_r)
    in_with_r = list(in_with(xs))
    print("in_with", in_with_r)
    in_match_r = list(in_match(xs))
    print("in_match", in_match_r)
    break_continue_r = list(break_continue(xs))
    print("break_continue", break_continue_r)
    return_in_finally_r = list(return_in_finally(xs))
    print("return_in_finally", return_in_finally_r)
    first = take_first(xs)
    print("abandoned", first)
    stop_r = list(stop_in_region(xs))
    print("stop_in_region", stop_r)
    total = asyncio.run(async_loop(xs))
    print("async_loop", total)
    try_total = asyncio.run(async_try_loop(xs))
    print("async_try_loop", try_total)

main()
