# Generic async free function: T is inferred from arg(s) at the await
# site. Two invariants exercised: (a) the callee's sub-coro struct
# carries `<T_substituted>` in the awaited frame slot, and (b) the
# awaited-value slot in the caller has the substituted type, not bare T.
import asyncio
from tpy import Int32


async def identity[T](x: T) -> T:
    return x


async def main_coro() -> None:
    result = await identity(Int32(42))  # tpyc: type(Int32)
    print(result)
    s = await identity("hi")  # tpyc: type(str)
    print(s)


def main() -> None:
    asyncio.run(main_coro())


main()
