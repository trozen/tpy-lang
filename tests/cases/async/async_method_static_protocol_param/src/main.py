# Regression: async method on a non-generic class taking an
# `Own[Cancellable[T]]`-shaped (static-protocol) param. Two codegen
# sites that previously emitted wrong C++ for this shape:
#   - In-struct template header (records.py) must include the
#     `T_<pname>` extra for the deduced sub-coro type.
#   - Factory body (generator.py) must `std::move(coro)` so the
#     T_coro&& ctor param binds when called from the factory's
#     T_coro&& lvalue param.
# The inner `coro` is forwarded into `asyncio.wait_for`; the await
# at the wait_for site drives the static-protocol param through
# both fixed code paths end-to-end.
import asyncio
from tpy import Own
from tpy.coro import Cancellable


class Runner:
    async def with_timeout[T](self, coro: Own[Cancellable[T]],
                              timeout: float) -> T:
        return await asyncio.wait_for(coro, timeout)


async def inner() -> int:
    await asyncio.sleep(0.001)
    return 13


async def main_coro() -> None:
    r = Runner()
    v = await r.with_timeout(inner(), 5.0)
    print(v)


def main() -> None:
    asyncio.run(main_coro())


main()
