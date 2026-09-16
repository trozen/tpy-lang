# print(sep=<expr>) / print(end=<expr>): a separator that has to be EVALUATED
# is bound to ONE temp and read by every gap in the chain. CPython evaluates a
# keyword argument once per call, so a three-argument print must run the source
# once -- the `Sep.tick` counter in the free section is the witness, since
# spelling the expression per gap would double both the allocation and the
# side effect.
import asyncio
from tpy import int32
from typing import Iterator


class Sep:
    calls: int32

    def __init__(self) -> None:
        self.calls = 0

    # each call bumps the counter, so the printed count is the number of
    # evaluations the chain performed
    def tick(self) -> str:
        self.calls += 1
        return "-"


class Row:
    a: str
    b: str

    def __init__(self, a: str, b: str) -> None:
        self.a = a
        self.b = b

    # method position: plain member reads are inert arguments, so the kwarg
    # temp keeps CPython's evaluation order
    def show(self, d: str) -> None:
        print("method:", end=" ")
        print(self.a, self.b, sep=d + ">")  # tpyc: ok


# free function: two gaps over one evaluated separator
def free_position(d: str) -> None:
    s = Sep()
    print("free:", end=" ")
    print(1, 2, 3, sep=s.tick())  # tpyc: ok
    print("free: evals", s.calls)
    print("free:", end=" ")
    print("a", "b", sep=d + "|")  # tpyc: ok
    print("free:", end=" ")
    print("tail", end=d + "!\n")  # tpyc: ok


# generator: the temp lands in a resumable case block
def gen(d: str) -> Iterator[int32]:
    print("gen:", end=" ")
    print("p", "q", sep=d + "%")  # tpyc: ok
    yield 1
    print("gen:", end=" ")
    print("r", "s", sep=d + "%")  # tpyc: ok


# async: the coroutine sibling
async def coro(d: str) -> None:
    print("async:", end=" ")
    print("u", "v", sep=d + "^")  # tpyc: ok
    await asyncio.sleep(0)


# closure: the separator source is a captured name
def closure_position(d: str) -> None:
    def show() -> None:
        print("closure:", end=" ")
        print("w", "x", sep=d + "+")  # tpyc: ok

    show()


# try/finally: the cleanup leg carries the evaluated end token
def try_position(d: str) -> None:
    try:
        print("try:", end=" ")
        print("y", "z", sep=d + "~")  # tpyc: ok
    finally:
        print("try: done", end=d + "\n")  # tpyc: ok


# match arm
def match_position(n: int32, d: str) -> None:
    match n:
        case 1:
            print("match:", end=" ")
            print("l", "m", sep=str(n) + d)  # tpyc: ok
        case _:
            print("match: other")


def main() -> None:
    d = "-"
    free_position(d)
    Row("x", "y").show(d)
    for v in gen(d):
        print("gen: yield", v)
    asyncio.run(coro(d))
    closure_position(d)
    try_position(d)
    match_position(1, d)


main()
