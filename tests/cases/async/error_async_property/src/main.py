# async @property is not supported -- properties present as field
# access syntax, but async methods need explicit call syntax to be
# awaited. Rejected at parse.
import asyncio


class P:
    @property
    async def value(self) -> int:  # tpyc: error(/async @property is not yet supported/)
        return 7


def main() -> None:
    print("ok")


main()
