# Async-with manager whose __aenter__ is async but __aexit__ is sync
# is rejected. Sibling of error_async_with_sync_manager (which has
# both methods sync); this case isolates the __aexit__ branch of the
# async-ness check.
import asyncio


class CM:
    async def __aenter__(self) -> int:
        return 1

    def __aexit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        pass


async def main_coro() -> None:
    async with CM() as v:  # tpyc: error(/`__aexit__` on '.+' must be `async def`/)
        print(v)


asyncio.run(main_coro())
