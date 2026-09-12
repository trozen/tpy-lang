# Async-def generic free function with an `Own[Awaitable[T]] | None`
# (nullable static-protocol) param. The codegen extension that
# supports `Own[Awaitable[T]]` async-def params only covers the
# single-required-protocol shape; nullable / multi-protocol shapes
# are rejected by `_protocol_template_parts` until a concrete need
# surfaces. This test guards that rejection path.
import asyncio
from tpy import int32
from tpy.coro import Awaitable


async def maybe_await[T](coro: Awaitable[T] | None, default: T) -> T:  # tpyc: error(/multi-protocol or optional-protocol shape is not yet supported/)
    return default


async def inner() -> int32:
    return int32(0)


async def main_coro() -> None:
    v = await maybe_await(inner(), int32(5))
    print(v)


def main() -> None:
    asyncio.run(main_coro())


main()
