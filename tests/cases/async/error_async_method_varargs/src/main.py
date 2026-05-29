# *args on an async method is rejected, same as for async free functions:
# the variadic coroutine-factory await lowering is not wired.
import asyncio


class W:
    async def g(self, *xs: int) -> None:  # tpyc: error(/\*args.* not yet supported on async methods/)
        await asyncio.sleep(0)


def main() -> None:
    print("ok")


main()
