# The owned element of a MIXED (owned + borrowed) tuple frame slot the body
# rebinds is rejected like the all-owned slot's: the rebind replaces the
# owned element in place under the alias
# (BUGS.md#resumable-alias-identity).
import asyncio

from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


async def co(b: A, c: bool) -> int32:
    t = (A(1), b)
    saved = t[0]  # tpyc: error(/not yet supported.*res\.alias_bind/)
    await asyncio.sleep(0)
    if c:
        t = (A(9), b)
    return saved.v


def main() -> None:
    print(asyncio.run(co(A(5), True)))


main()
