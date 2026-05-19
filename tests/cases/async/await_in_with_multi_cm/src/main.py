# Two context managers in a single `with` stmt, with `await` in
# the body (v1.5 M3.2). Verifies that `_build_with` /
# `_prescan_with_stmts` handle N items: per-CM frame slot,
# per-CM region wrap, innermost-first __exit__ on normal exit.
import asyncio


class Tracer:
    def __init__(self, label: str) -> None:
        self.label = label

    def __enter__(self) -> None:
        print(f"enter {self.label}")

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(f"exit {self.label}")


async def value(n: int) -> int:
    return n


async def caller() -> int:
    with Tracer("A"), Tracer("B"):
        x = await value(7)
    return x


def main() -> None:
    print(asyncio.run(caller()))


main()
