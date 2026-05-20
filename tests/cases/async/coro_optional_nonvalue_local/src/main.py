# Regression: coroutine-frame Optional[NonValue] local. The frame slot
# used to be std::optional<std::optional<T>> (double-wrap for the
# "uninitialized" outer + storage form inner), which (a) fails to
# compile when T is @nocopy (deleted inner copy ctor) and (b) is
# semantically a hidden copy for any T -- diverging from CPython's
# reference semantics. The frame slot is now `T* = nullptr` (nullptr
# serves as both "uninitialized" and "None"), aliasing whatever the
# assignment source pointed at. Exercises @nocopy P inner +
# survival across a suspension boundary (await). Also exercises an
# explicit `h = None` reassign inside the coroutine to hit the
# pointer-local rebind path's None-literal branch.
import asyncio
from tpy import nocopy, Int32


@nocopy
class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def maybe_p(items: list[P], i: Int32) -> P | None:
    if i < len(items):
        return items[i]
    return None


async def pick(items: list[P], i: Int32, drop: bool) -> Int32:
    # h is a pointer-repr Optional local that must survive the await.
    h = maybe_p(items, i)
    if drop:
        h = None
    await asyncio.sleep(0)
    if h is not None:
        return h.x
    return -1


async def driver() -> None:
    items: list[P] = []
    items.append(P(10))
    items.append(P(20))
    items.append(P(30))
    print(await pick(items, 0, False))
    print(await pick(items, 2, False))
    print(await pick(items, 5, False))
    print(await pick(items, 0, True))


asyncio.run(driver())
