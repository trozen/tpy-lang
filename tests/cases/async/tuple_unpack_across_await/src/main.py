# Value-type tuple-unpack targets must survive an await: the unpacked locals
# are frame fields, so a fresh shadowing C++ local would read a stale 0 after
# the suspension. Regression guard for tuple-unpack-across-await.
import asyncio


def make_pair() -> tuple[int, int]:
    return (1, 2)


async def f() -> int:
    a, b = make_pair()
    await asyncio.sleep(0)
    return a + b


def main() -> None:
    print(asyncio.run(f()))


main()
