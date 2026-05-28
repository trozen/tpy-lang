# Handler returns one value, then the suspending finally returns another
# -- finally wins (Python). Both writes hit the same pending-return slot.
# Expected: caller returns "from_finally".
import asyncio


async def cleanup() -> None:
    await asyncio.sleep(0)


async def caller() -> str:
    try:
        raise ValueError("oops")
    except ValueError:
        return "from_handler"
    finally:
        await cleanup()
        return "from_finally"


def main() -> None:
    print(asyncio.run(caller()))


main()
