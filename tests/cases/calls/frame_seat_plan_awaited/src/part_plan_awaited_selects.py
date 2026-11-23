# A `str` ternary whose arm is an `and` / `or` select: the arm is a view only
# where its own lowering made it one, so an arm holding a fresh value keeps
# the ternary owned -- in plain functions, a generator, and an awaited
# coroutine argument.
import asyncio
from typing import Iterable, Iterator
from tpy import char


def sel_f(c: bool, p: str, q: str) -> str:
    s = f"{q}x"
    r = s if c else (p or "zz")  # tpyc: ok
    return r


def sel_g(c: bool, p: str) -> str:
    # both arms views: the ternary is a view of `p` or the literal
    v = p if c else (p and "yy")  # tpyc: ok
    return v


def sel_h(c: bool, p: str, q: str) -> str:
    s = f"{q}!"
    v = (p or "zz") if c else s  # tpyc: ok
    s = "changed"
    return v


def sel_gen(c: bool, p: str) -> Iterator[str]:
    v = "lit" if c else (p or "zz")  # tpyc: ok
    yield v
    yield v


def sel_fresh(c: bool, d: bool, p: str) -> str:
    # an `or` over a fresh value, an `or` with a nested ternary arm, and an
    # `or` over a ternary of views
    v = p if c else (f"{p}?" or "zz")  # tpyc: ok
    w = p if c else (p or ("a" if d else f"{p}!"))  # tpyc: ok
    u = p if c else ((p if d else "q") or "zz")  # tpyc: ok
    return v + w + u


async def collect(xs: Iterable[char]) -> str:
    await asyncio.sleep(0)
    junk = ["X" * 24, "Y" * 24, "Z" * 24]
    out = ""
    for x in xs:
        out += str(x)
    return out + "|" + junk[0][:2]


async def sel_awaited(s: str | None, t: str, flag: bool) -> str:
    if s is None:
        return "none"
    # the `or` copies `t` into a fresh string: the awaited ternary owns it
    return await collect((s or t) if flag else "fallback")  # tpyc: ok


def main() -> None:
    print("sel f", sel_f(False, "", "q"), sel_f(True, "p", "q"),
          sel_f(False, "pp", "q"))
    print("sel g", sel_g(False, "p"), sel_g(True, "p"))
    print("sel h", sel_h(True, "", "q"), sel_h(False, "p", "q"))
    for x in sel_gen(False, "pp"):
        print("sel gen", x)
    print("sel fresh", sel_fresh(False, False, "x"),
          sel_fresh(False, True, ""), sel_fresh(True, True, "y"))
    print("sel awaited", asyncio.run(sel_awaited("", "abcdefghijklmnopqrstuvwx", True)))
    print("sel awaited", asyncio.run(sel_awaited("ABCDEFGHIJKLMNOPQRSTUVWX", "other", True)))
    print("sel awaited", asyncio.run(sel_awaited("", "unused", False)))
