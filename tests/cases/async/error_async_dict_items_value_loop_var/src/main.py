# A whole-variable loop over `dict.items()` with a value element type across
# a suspension: only unpack holders take the value-tuple frame bind.
import asyncio
from tpy import Int32


async def step(n: Int32) -> Int32:
    return n + 1


async def f() -> Int32:  # tpyc: error(/res\.loop_var/)
    d = {1: 2}
    total = 0
    # `kv` is a whole value-tuple loop variable living across the await.
    for kv in d.items():
        total = await step(kv[1])
    return total


def main() -> None:
    print(asyncio.run(f()))


main()
