# `break` / `continue` inside a CFG-lowered `for`-with-await
# body translate to state transitions, not C++ break/continue.
import asyncio

async def value(n: int) -> int:
    return n

async def first_match() -> int:
    total = 0
    for i in range(10):
        if i == 3:
            continue
        if i >= 6:
            break
        total = total + await value(i)
    return total

def main() -> None:
    print(asyncio.run(first_match()))

main()
