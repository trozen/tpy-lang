# The owned element of a MIXED (owned + borrowed) tuple frame slot the body
# rebinds is rejected like the all-owned slot's: the rebind replaces the
# owned element in place under the alias
# (BUGS.md#resumable-alias-identity). Binding a copy is no workaround
# here yet: `copy(t[0])` of a mixed slot's element does not compile
# (BUGS.md#tuple-elem-copy-mixed-or-list-rejects).
import asyncio

from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


async def co(b: A, c: bool) -> int32:
    t = (A(1), b)
    saved = t[0]  # tpyc: error(/binding 'saved' to an element inside 't' is not yet supported in an async function.*last reassignment of 't'$/)
    await asyncio.sleep(0)
    if c:
        t = (A(9), b)
    return saved.v


def main() -> None:
    print(asyncio.run(co(A(5), True)))


main()
