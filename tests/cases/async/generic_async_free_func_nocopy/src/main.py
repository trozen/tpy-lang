# Generic async free function over a non-value type (Box[T] is @nocopy):
# the coro struct must use `val_or_ref_t<T>` for fields and
# `param_val_or_ref_t<T>` for ctor params so non-value Ts pass and
# store by reference without triggering the deleted copy. Mirrors the
# task_generic_param_nocopy precedent on the sync side.
import asyncio
from tpy import int32
from tplib import Box


async def unwrap[T](b: Box[T]) -> T:
    return b.get()


async def main_coro() -> None:
    box = Box(int32(99))
    result = await unwrap(box)  # tpyc: type(int32)
    print(result)


def main() -> None:
    asyncio.run(main_coro())


main()
