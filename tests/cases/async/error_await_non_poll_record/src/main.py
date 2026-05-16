# `await` on a record that does not implement __poll__(Waker) -> Poll[T]
# is rejected by _extract_awaitable_inner's structural check. Pins the
# error path since the old `_module_qname == "tpy.Task"` fast-path is gone
# and the structural fallback is now the only safety net.
class NotAwaitable:
    x: int

    def __init__(self) -> None:
        self.x = 0


async def m() -> None:
    bad = NotAwaitable()
    await bad  # tpyc: error(/await operand must be a direct call to an async def/)


import asyncio
asyncio.run(m())
