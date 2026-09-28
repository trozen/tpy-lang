# A frame alias target off a borrow-tuple call whose argument is a hoisted
# temp: the alias field would outlive the case block the temp dies with.
import asyncio


class M:
    v: int

    def __init__(self) -> None:
        self.v = 3


def pick(m: M) -> tuple[M, int]:
    return m, m.v


async def co() -> int:
    a, n = pick(M())  # tpyc: error(/res\.unpack/)
    await asyncio.sleep(0)
    a.v = 9
    return a.v + n


def main() -> None:
    print(asyncio.run(co()))


main()
