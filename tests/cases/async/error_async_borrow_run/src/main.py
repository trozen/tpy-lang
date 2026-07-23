# A borrow-returning coroutine is direct-await-only: asyncio.run stores
# an owned result, so passing one is a compile error naming the Own
# escape hatch.
import asyncio


class Server:
    n: int

    def __init__(self) -> None:
        self.n = 1

    async def get(self) -> "Server":
        await asyncio.sleep(0)
        return self


def main() -> None:
    s = Server()
    r = asyncio.run(s.get())  # tpyc: error(/borrow-returning coroutine/)
    print(r.n)


main()
