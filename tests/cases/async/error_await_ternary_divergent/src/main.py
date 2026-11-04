# A ternary whose two await branches have different types is rejected: the
# desugar lowers the branches into one result temp, and TPy does not widen a
# temp across branches (same as sync `a if c else b` with divergent types).
import asyncio


async def an_int() -> int:
    return 1


async def a_str() -> str:
    return "x"


async def main(cond: bool) -> None:  # a parameter: sema does not decide `cond`
    x = await an_int() if cond else await a_str()  # tpyc: error(/mismatch|differ|type/)
    print(x)


asyncio.run(main(True))
