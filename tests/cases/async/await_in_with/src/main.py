# Sync `with X:` body containing `await` (v1.5 M3.2). The context
# manager lives in a `__with_ctx_<n>` frame slot; the case-emit
# wraps each case in try/catch so __exit__ runs on the throw path.
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
    with Tracer("outer"):
        x = await value(7)
    return x


def main() -> None:
    print(asyncio.run(caller()))


main()
