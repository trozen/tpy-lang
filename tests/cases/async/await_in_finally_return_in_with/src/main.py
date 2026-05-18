# `return` inside a `with` body that is itself nested inside a
# `try` / `finally`-with-await (M3.3.2 + WithRegion interaction).
# The return must:
#   1. Run the with-stmt's `__exit__(None, None, None)` (the
#      WithRegion sits above the CFG-finally boundary in the
#      pending-return chain).
#   2. Park the value in `__finally_ret_<n>` + set the flag.
#   3. Transition to the finally entry.
#   4. After the finally body runs, AsyncFinallyExit emits the
#      deferred Poll::ready.
# Expected order: "exit-with" -> "cleanup" -> 7
import asyncio


class Tracer:
    def __init__(self, label: str) -> None:
        self.label = label

    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print(f"exit-{self.label}")


async def value(n: int) -> int:
    return n


async def cleanup() -> None:
    print("cleanup")


async def caller() -> int:
    try:
        with Tracer("with"):
            x = await value(7)
            return x
    finally:
        await cleanup()


def main() -> None:
    print(asyncio.run(caller()))


main()
