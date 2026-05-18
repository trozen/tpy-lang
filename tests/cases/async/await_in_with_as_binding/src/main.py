# `with X() as t:` binding works inside async-def -- the as-var is
# a hoisted frame field so awaits in the body see the bound value
# across suspensions.
import asyncio


class Resource:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(f"exit {self.name}")


async def value(n: int) -> int:
    return n


async def caller() -> None:
    with Resource("R") as label:
        x = await value(3)
        print(f"{label}={x}")


def main() -> None:
    asyncio.run(caller())


main()
