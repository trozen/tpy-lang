# `return t if f else u` at a value-tuple return: the C++ conditional over the
# two tuples is the render, the same shape the literal, the name and the call
# sources already take at that return. A value tuple copies at every sink, so
# neither arm raises the aliasing question a reference-typed ternary does, and
# the conditional is a plain value select. A LITERAL arm beside a name arm pins
# to a declared return; without one it is refused by sema
# (BUGS.md#literal-elem-tuple-ternary-mismatch), and a generator YIELD of the
# same ternary rejects one rung on (res.btuple_yield_source).
import asyncio
from tpy import int32


class Pair:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    # method position
    def pick(self, f: bool) -> tuple[int32, str]:
        t = (self.base, "m")
        u = (self.base + 1, "n")
        return t if f else u  # tpyc: ok


# free function: both arms are names
def name_arms(f: bool) -> tuple[int32, str]:
    t = (1, "a")
    u = (2, "b")
    return t if f else u  # tpyc: ok


# async: the coroutine sibling
async def coro(f: bool) -> tuple[int32, str]:
    t = (7, "g")
    u = (8, "h")
    await asyncio.sleep(0)
    return t if f else u  # tpyc: ok


def main() -> None:
    a = name_arms(True)
    b = name_arms(False)
    print("names:", a[0], a[1], b[0], b[1])
    p = Pair(10).pick(True)
    print("method:", p[0], p[1])
    c = asyncio.run(coro(True))
    print("async:", c[0], c[1])


main()
