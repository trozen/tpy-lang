# Sync `with X:` whose `__exit__` returns bool (can suppress)
# inside an async-def. After a suppressed exception, control
# transitions to `post_with_bb` (a synthesized case label).
import asyncio


class Suppressor:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> str:
        return self.name

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is not None:
            print(f"{self.name} suppressing")
            return True
        return False


async def value(n: int) -> int:
    return n


async def caller() -> int:
    with Suppressor("S") as s:
        x = await value(2)
        raise ValueError(s)
    return 42


def main() -> None:
    print(asyncio.run(caller()))


main()
