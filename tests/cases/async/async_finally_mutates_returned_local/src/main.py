# Async sibling: a (non-suspending) finally mutating a returned reference-type
# local is visible in the returned object, matching CPython aliasing.
import asyncio
from tpy import Int32, Own


class Box:
    n: Int32

    def __init__(self) -> None:
        self.n = 10


async def f() -> Own[Box]:
    b = Box()
    try:
        return b
    finally:
        b.n += 1


async def f_opt(flag: bool) -> Own[Box] | None:
    b: Box | None = None
    if flag:
        b = Box()
    try:
        return b
    finally:
        if b is not None:
            b.n += 1


# This case should also test mutation through a closure, like the sync
# via_closure case does. It can't yet: a nested def that captures a local
# does not compile inside an async def (BUGS.md). Add that shape once it
# works.
async def f_alias() -> Own[Box]:
    b = Box()
    a = b
    try:
        return b
    finally:
        a.n += 1


async def main() -> None:
    r = await f()
    print(r.n)
    o = await f_opt(True)
    if o is not None:
        print(o.n)
    print(await f_opt(False) is None)
    print((await f_alias()).n)


asyncio.run(main())
