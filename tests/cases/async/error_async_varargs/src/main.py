# *args on an async function is rejected: the variadic coroutine-factory await
# lowering is not wired and would miscompile to opaque C++.
import asyncio


async def f(*xs: int) -> None:  # tpyc: error(/\*args.* not yet supported on async functions/)
    await asyncio.sleep(0)


def main() -> None:
    print("ok")


main()
