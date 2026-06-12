# Async sibling of finally_return_eval_order: the Poll-ready value must be
# captured before the (non-suspending) finally body runs.
import asyncio


async def f() -> int:
    x = 1
    try:
        await asyncio.sleep(0)
        return x
    finally:
        x = 2


def main() -> None:
    print(asyncio.run(f()))


main()
