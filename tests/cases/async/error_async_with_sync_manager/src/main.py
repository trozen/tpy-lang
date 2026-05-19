# A context manager whose __aenter__/__aexit__ are NOT async def
# is rejected (use `with`, not `async with`).
import asyncio


class CM:
    def __aenter__(self) -> int:
        return 1

    def __aexit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        pass


async def main_coro() -> None:
    async with CM() as v:  # tpyc: error(/`__aenter__` on '.+' must be `async def`/)
        print(v)


asyncio.run(main_coro())
