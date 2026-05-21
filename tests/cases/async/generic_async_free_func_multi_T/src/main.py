# Two-type-param generic async free function: codegen must emit both
# inferred type args on the sub-coro frame field (`__coro_pair<K, V>`).
import asyncio
from tpy import Int32


async def make_pair[K, V](k: K, v: V) -> tuple[K, V]:
    return (k, v)


async def main_coro() -> None:
    p = await make_pair(Int32(7), "x")  # tpyc: type(/tuple\[Int32, str\]/)
    print(p[0])
    print(p[1])


def main() -> None:
    asyncio.run(main_coro())


main()
