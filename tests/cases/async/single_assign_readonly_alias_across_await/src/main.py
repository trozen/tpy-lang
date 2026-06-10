# A borrow alias of a readonly source held across an await must be a `const T*`
# frame field, not a mutable `T*` (else the C++ build fails on const-correctness).
# Guards the const-alias arm of the across-suspension frame hoist.
import asyncio
from tpy import readonly


class Inner:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class Outer:
    inner: Inner

    def __init__(self, n: int) -> None:
        self.inner = Inner(n)


async def peek(o: readonly[Outer]) -> int:
    a = o.inner
    await asyncio.sleep(0)
    return a.n


async def amain() -> None:
    print(await peek(Outer(9)))


asyncio.run(amain())
