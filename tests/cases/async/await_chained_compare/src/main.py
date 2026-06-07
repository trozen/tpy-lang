# Chained comparison with awaited operands: each operand is evaluated
# exactly once, and the chain short-circuits on the first false comparison
# (the operand after a false comparison is not awaited).
import asyncio


async def val(tag: str, n: int) -> int:
    print("eval", tag)
    return n


async def main() -> None:
    # 1 < 5 < 3: (1<5) True so c IS evaluated; (5<3) False -> overall False.
    # Each of a/b/c awaited once.
    r = await val("a", 1) < await val("b", 5) < await val("c", 3)
    print("r", r)
    # 9 < 5 < ...: (9<5) False -> short-circuit, third operand NOT awaited.
    r2 = await val("a2", 9) < await val("b2", 5) < await val("c2-skipped", 3)
    print("r2", r2)


asyncio.run(main())
