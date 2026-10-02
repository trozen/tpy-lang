# print() with sep=/end= and NO positional args: the chain is the end token
# alone, and `end=""` suppresses it so the statement emits nothing at all.
# A sep with nothing to separate never reaches the chain.
import asyncio
import sys
from typing import Iterator


class Emitter:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    # method position
    def emit(self) -> None:
        print(end="")  # tpyc: ok
        print(self.tag, end="")
        print(end="|\n")  # tpyc: ok


# free function: every argument-less kwarg form
def free_position() -> None:
    print("free:", end="")
    print(end="")  # tpyc: ok -- emits only the Ctrl-C check point
    print(end="a")  # tpyc: ok
    print(sep=",")  # tpyc: ok -- sep has nothing to separate, so just "\n"
    print(sep=",", end="b\n")  # tpyc: ok
    suffix = "c\n"
    print(end=suffix)  # tpyc: ok -- a runtime end with no args
    print(file=sys.stdout, end="d\n")  # tpyc: ok
    print(end="", flush=True)  # tpyc: ok -- flush alone, nothing written
    print(end="e\n", flush=True)  # tpyc: ok


# generator: the suppressed chain sits in a resumable case block
def gen() -> Iterator[int]:
    print("gen:", end="")
    print(end="")  # tpyc: ok
    yield 1
    print(end="g\n")  # tpyc: ok


# async: the coroutine sibling
async def coro() -> int:
    print("async:", end="")
    print(end="")  # tpyc: ok
    await asyncio.sleep(0)
    print(end="h\n")  # tpyc: ok
    return 2


def try_position() -> None:
    print("try:", end="")
    try:
        print(end="")  # tpyc: ok
    finally:
        print(end="i\n")  # tpyc: ok


def main() -> None:
    free_position()
    Emitter("method:").emit()
    for v in gen():
        print("yield", v)
    # bound first: a print argument that itself writes to stdout interleaves
    # ahead of the earlier arguments (BUGS.md#subexpression-right-to-left-eval)
    got = asyncio.run(coro())
    print("ran", got)
    try_position()


main()
