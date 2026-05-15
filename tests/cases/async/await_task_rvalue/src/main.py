# Regression: `await asyncio.create_task(...)` directly on the rvalue
# (no local binding) -- exercises the ERASED-mode path for an
# Own[Task[T]] temporary. The frame move-constructs the Task into its
# std::optional<Task<T>> sub-future slot; the lvalue-borrow path (which
# would try to take the address of a temporary) is correctly avoided.
import asyncio
from tpy import Int32


async def sub() -> Int32:
    return Int32(42)


async def main_coro() -> None:
    # Inline rvalue await: no `t = ...` binding step. Pre-fix this
    # would have hit a sema/codegen rejection because the operand
    # wasn't a stable lvalue and there was no consume path.
    result = await asyncio.create_task(sub())
    print(result)


def main() -> None:
    asyncio.run(main_coro())


main()
