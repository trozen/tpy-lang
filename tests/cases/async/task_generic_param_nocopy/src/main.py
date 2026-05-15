# Regression: generic and concrete Task[T] params are emitted by
# reference at the C++ signature. Previously tpy.Task was registered
# as is_value_type=True, so both `t: Task[T]` (generic) and
# `t: Task[Int32]` (concrete) emitted `Task<T> t` by-value at the
# generated C++ signature -- breaking the deleted copy ctor at the
# call site. Now Task[T] is correctly is_value_type=False (matching
# its @nocopy nature), so concrete returns spell `Own[Task[T]]` and
# params spell `const Task<T>&`.
import asyncio
from tpy import Int32
from tpy.coro import Task


async def co(x: Int32) -> Int32:
    return x + Int32(1)


def task_arity_concrete(t: Task[Int32]) -> Int32:
    # Signature only: param is a Task[Int32] borrow. Body proves the
    # codegen path accepts the binding without copying.
    return 0


def task_arity_generic[T](t: Task[T]) -> Int32:
    # Same as above, generic form. Pre-fix this emitted `Task<T> t`
    # by-value and the call site failed to compile.
    return 1


async def main_coro() -> None:
    t = asyncio.create_task(co(Int32(7)))
    _ = task_arity_concrete(t)
    _ = task_arity_generic[Int32](t)
    result = await t
    print(result)


def main() -> None:
    asyncio.run(main_coro())


main()
