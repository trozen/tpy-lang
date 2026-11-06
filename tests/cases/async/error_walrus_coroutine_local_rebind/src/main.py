# A walrus rebinding a coroutine-handle local in an `async def` is refused
# (BUGS.md#walrus-coroutine-local-rebind-rejects).
import asyncio


async def value(n: int) -> int:
    return n


async def run() -> None:
    x = value(1)
    # The rebind to another coroutine is the subject.
    (x := value(2))  # tpyc: warning(/drops the previous coroutine without running it/) error(/this construct is not yet supported by C\+\+ code generation \(expr.walrus\)/)
    print(await x)


asyncio.run(run())
