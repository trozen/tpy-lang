# A generic async def bound to a local: the concrete frame slot carries
# the substituted template args (std::optional<__coro_ident<T>>).
import asyncio


async def ident[T](x: T) -> T:
    return x


async def main_coro() -> None:
    c = ident(41)
    print(await c)
    s = ident("ok")
    print(await s)


def main() -> None:
    asyncio.run(main_coro())


main()
