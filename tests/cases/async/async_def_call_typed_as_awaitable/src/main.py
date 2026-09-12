# Regression: `f()` for `async def f() -> T` is sema-typed as
# `Cancellable[T]`, which structurally extends `Awaitable[T]` via
# `__poll__`, so passing the call result to a generic helper expecting
# `Awaitable[T]` still infers T from the protocol arg's poll return type.
from tpy import int32
from tpy.coro import poll_once


async def compute() -> int32:
    return int32(42)


def main() -> None:
    print(poll_once(compute()).value())


main()
