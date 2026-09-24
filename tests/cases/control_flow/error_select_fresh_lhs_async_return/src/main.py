# An async return of an and/or whose LHS is fresh rejects: the LHS temp has
# no statement flush there (BUGS.md#select-fresh-lhs-positions).
import asyncio
from tpy import int32, Own


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __bool__(self) -> bool:
        return self.n != 0


def make() -> Own[C]:
    return C(10)


# The async body's reject is reported at its `def` line.
async def pick() -> Own[C]:  # tpyc: error(/not yet supported/)
    await asyncio.sleep(0)
    # The subject: the fresh `C(0)` would be evaluated once into a temp and
    # moved out.
    return C(0) or make()


def main() -> None:
    print(asyncio.run(pick()).n)


main()
