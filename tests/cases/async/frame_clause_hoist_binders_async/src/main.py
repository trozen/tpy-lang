# The async twin of generators/frame_clause_hoist_binders: a local first
# bound inside a clause of an `async def` by a tuple unpack, a walrus or a
# `with ... as` target is one frame field, written by the clause and read
# after the await.
import asyncio
from tpy import int32


class Guard:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Cell:
    v: int

    def __init__(self, v: int):
        self.v = v


class Source:
    tag: str

    def __init__(self):
        self.tag = "m"

    # async METHOD: the clause-bound names are fields of the method's frame
    async def pairs(self) -> str:
        for i in [1, 2]:
            s, k = pair()  # tpyc: ok
        n = await value(8)
        return f"{self.tag}/{s}/{k}/{n}"


def pair() -> tuple[str, int]:
    return ("a", 7)


def with_cell(c: Cell) -> tuple[Cell, int]:
    return (c, 7)


def flag() -> bool:
    return True


def fallback() -> tuple[str, int]:
    return ("z", 0)


def num() -> int:
    return 7


async def value(n: int) -> int:
    return n


# for BODY
async def a_for_body() -> str:
    for i in [1, 2]:
        s, k = pair()  # tpyc: ok
    n = await value(1)
    return f"{s}/{k}/{n}"


# for ELSE
async def a_for_else() -> str:
    for i in [1, 2]:
        pass
    else:
        s, k = pair()  # tpyc: ok
    n = await value(2)
    return f"{s}/{k}/{n}"


# while BODY
async def a_while_body() -> str:
    i = 0
    while i < 2:
        s, k = pair()  # tpyc: ok
        i += 1
    n = await value(3)
    return f"{s}/{k}/{n}"


# try body + except arm
async def a_try_except() -> str:
    try:
        s, k = pair()  # tpyc: ok
    except ValueError:
        s, k = fallback()
    n = await value(4)
    return f"{s}/{k}/{n}"


# if / else, BOTH arms call-sourced
async def a_if_arms() -> str:
    if flag():
        s, k = pair()  # tpyc: ok
    else:
        s, k = pair()
    n = await value(9)
    return f"{s}/{k}/{n}"


# with BODY
async def a_with_body() -> str:
    with Guard() as t:
        s, k = pair()  # tpyc: ok
    n = await value(5)
    return f"{s}/{k}/{n}"


# `with ... as` TARGET -- the third binder kind, read after the await
async def a_with_target() -> str:
    for i in [1, 2]:
        with Guard() as t:  # tpyc: ok
            pass
    n = await value(10)
    return f"{t}/{n}"


# leaf match arm
async def a_match_arm(m: int32) -> str:
    match m:
        case 1:
            s, k = pair()  # tpyc: ok
        case _:
            s, k = fallback()
    n = await value(6)
    return f"{s}/{k}/{n}"


# borrowed record target in a loop body: the target aliases the caller's
# record, so the mutation after the await is visible to the caller
async def a_record(c: Cell) -> str:
    for i in [1, 2]:
        r, k = with_cell(c)  # tpyc: ok
    n = await value(11)
    r.v = 88
    return f"{k}/{n}"


# the same borrowed record target in a leaf match arm
async def a_record_match(c: Cell, m: int32) -> str:
    match m:
        case 1:
            r, k = with_cell(c)  # tpyc: ok
        case _:
            r, k = with_cell(c)
    n = await value(12)
    r.v = 89
    return f"{k}/{n}"


# walrus in a loop clause
async def a_walrus() -> str:
    for i in [1, 2]:
        if (w := num()):  # tpyc: ok
            pass
    n = await value(7)
    return f"{w}/{n}"


async def driver() -> None:
    print("for_body:", await a_for_body())
    print("for_else:", await a_for_else())
    print("while_body:", await a_while_body())
    print("try_except:", await a_try_except())
    print("if_arms:", await a_if_arms())
    print("with_body:", await a_with_body())
    print("with_target:", await a_with_target())
    print("match_arm:", await a_match_arm(1))
    src = Source()
    print("method:", await src.pairs())
    c = Cell(1)
    print("record:", await a_record(c))
    print("record: caller sees", c.v)
    cm = Cell(1)
    print("record_match:", await a_record_match(cm, 1))
    print("record_match: caller sees", cm.v)
    print("walrus:", await a_walrus())


def main() -> None:
    asyncio.run(driver())


main()
