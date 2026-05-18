# `for ... else:` with `await` in the loop body (not the else
# clause -- that's still rejected). Positive coverage for the
# orelse path of `_build_for` when the orelse is a no-await leaf.
# The else clause runs after the loop exits normally (no `break`).
import asyncio


async def value(n: int) -> int:
    return n


async def caller() -> int:
    total = 0
    for i in range(3):
        total = total + await value(i)
    else:
        print("else-ran")
    return total


def main() -> None:
    print(asyncio.run(caller()))


main()
