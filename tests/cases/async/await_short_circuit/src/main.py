# `await` in short-circuit `or`/`and` must short-circuit AND be
# value-returning (like CPython / sync TPy: `a or b` is `a` if truthy else
# `b`, not a bool). Non-bool (int) operands make the value-vs-bool
# distinction observable, and the prints observe which operands ran.
import asyncio


async def num(tag: str, n: int) -> int:
    print("eval", tag)
    return n


async def main() -> None:
    # or, left truthy: right skipped, result is the left value (5, not True)
    r1 = await num("or1-left", 5) or await num("or1-right-skip", 9)
    print("r1", r1)
    # or, left falsy: right evaluated, result is the right value
    r2 = await num("or2-left", 0) or await num("or2-right-run", 7)
    print("r2", r2)
    # and, left falsy: right skipped, result is the left value (0)
    r3 = await num("and1-left", 0) and await num("and1-right-skip", 9)
    print("r3", r3)
    # and, left truthy: right evaluated, result is the right value (8)
    r4 = await num("and2-left", 3) and await num("and2-right-run", 8)
    print("r4", r4)


asyncio.run(main())
