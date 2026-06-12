# async-with sibling of with_manager_consume: __aexit__ runs on the
# manager after the body, so a consume inside the body must copy (with
# warning), not move.
import asyncio
from tpy import Int32, Own


class Guard:
    vals: list[Int32]

    def __init__(self):
        self.vals = [1, 2]

    async def __aenter__(self) -> None:
        pass

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        print("exit sees", len(self.vals))


class K:
    stored: list[Guard]

    def __init__(self):
        self.stored = []

    def take(self, g: Own[Guard]):
        self.stored.append(g)


async def runner() -> None:
    k = K()
    g = Guard()
    async with g:
        k.take(g)  # tpyc: warning(/copies/)


def main():
    asyncio.run(runner())


main()
