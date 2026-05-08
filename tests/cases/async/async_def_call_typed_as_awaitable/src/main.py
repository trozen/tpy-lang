# Regression: `f()` for `async def f() -> T` is sema-typed as
# `Awaitable[T]`, so passing it to a generic helper expecting
# `Awaitable[T]` infers T from the protocol arg's poll return type.
from tpy import Int32
from tpy.coro import poll_once


async def compute() -> Int32:
    return Int32(42)


def main() -> None:
    print(poll_once(compute()).value())


main()
