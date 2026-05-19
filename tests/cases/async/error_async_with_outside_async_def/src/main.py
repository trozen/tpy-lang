# `async with` is only allowed inside `async def`. Outside, sema
# rejects with a clear pointer.
import asyncio


class CM:
    async def __aenter__(self) -> int:
        return 1

    async def __aexit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        pass


def main() -> None:
    async with CM():  # tpyc: error(/`async with` is only allowed inside an `async def`/)
        print("nope")


main()
