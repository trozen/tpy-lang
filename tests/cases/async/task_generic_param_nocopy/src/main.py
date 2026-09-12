# Regression: generic and concrete Task[T] params are emitted by
# reference at the C++ signature. Previously tpy.Task was registered
# as is_value_type=True, so both `t: Task[T]` (generic) and
# `t: Task[int32]` (concrete) emitted `Task<T> t` by-value at the
# generated C++ signature -- breaking the deleted copy ctor at the
# call site. Now Task[T] is correctly is_value_type=False (matching
# its @nocopy nature), so concrete returns spell `Own[Task[T]]` and
# params spell `const Task<T>&`.
import asyncio
from tpy import int32
from asyncio import Task


async def co(x: int32) -> int32:
    return x + int32(1)


def task_arity_concrete(t: Task[int32]) -> int32:
    # Signature only: param is a Task[int32] borrow. Body proves the
    # codegen path accepts the binding without copying.
    return 0


def task_arity_generic[T](t: Task[T]) -> int32:
    # Same as above, generic form. Pre-fix this emitted `Task<T> t`
    # by-value and the call site failed to compile.
    return 1


async def main_coro() -> None:
    t = asyncio.create_task(co(int32(7)))
    _ = task_arity_concrete(t)
    _ = task_arity_generic[int32](t)
    result = await t
    print(result)


def main() -> None:
    asyncio.run(main_coro())


main()
