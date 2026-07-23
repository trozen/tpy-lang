# `async with server as s`: __aenter__ returning `self` is a borrow
# (pointer Poll payload), so `s` ALIASES the manager -- mutations through
# the as-binding are visible on the original and in __aexit__ (CPython).
import asyncio


class Server:
    n: int

    def __init__(self) -> None:
        self.n = 1

    async def __aenter__(self) -> "Server":
        await asyncio.sleep(0)
        return self

    async def __aexit__(self, exc_type: None, exc_val: None,
                        exc_tb: None) -> None:
        await asyncio.sleep(0)
        print("exit", self.n)


async def main() -> None:
    server = Server()
    async with server as s:
        s.n = 7
    print(server.n)


asyncio.run(main())
