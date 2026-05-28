# Error: a `return` inside a `finally` body that itself awaits is a planned
# follow-up -- the CFG-based finally can't yet park the pending return
# across the suspending finally, so sema rejects it early. A `return` in
# the try body (not the finally) stays supported. Async analog of
# iterators/error_gen_return_in_yield_finally.
import asyncio


async def cleanup() -> None:
    await asyncio.sleep(0)


async def caller() -> int:
    try:  # tpyc: error(/return. inside a finally body that itself contains/)
        return 1
    finally:
        await cleanup()
        return 2


def main() -> None:
    print(asyncio.run(caller()))


main()
