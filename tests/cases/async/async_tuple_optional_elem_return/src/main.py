# Async value-tuple return of a tuple LITERAL with a value-repr Optional
# element: the render must target the return slot (None -> std::nullopt,
# a scalar absorbed by the optional's converting ctor), not spell the
# element's own type (regression: untargeted monostate/nullptr render).
import asyncio
from tpy import int32


async def pick(n: int32) -> tuple[int32, int32 | None]:
    if n > 0:
        return (n, n * 2)
    return (n, None)


async def main_coro() -> None:
    a = await pick(5)
    v = a[1]
    if v is not None:
        print(a[0], v)
    b = await pick(-1)
    print(b[0], b[1] is None)


def main() -> None:
    asyncio.run(main_coro())


main()
