# Finally body with two sequential `await` calls (v1.5 M3.3).
# Each suspension inside the finally body emits its own Yield
# terminator and its own resume case; AsyncFinallyExit only fires
# at the tail after both have completed.
import asyncio


async def first() -> None:
    print("first")


async def second() -> None:
    print("second")


async def caller() -> int:
    try:
        return 5
    finally:
        await first()
        await second()


def main() -> None:
    print(asyncio.run(caller()))


main()
